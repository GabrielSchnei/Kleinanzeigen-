"""Hauptprogramm: Scan-Schleife, Deal-Pipeline, Telegram-Steuerung, Morgen-Überblick."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import asdict
from datetime import datetime

from .ai_filter import AIFilter
from .analyzer import analyze, posted_minutes_ago
from .config import Config
from .market import Market
from .messenger import Messenger, MessengerError
from .models import AIVerdict, Analysis, Listing
from .schedule import is_active, last_quiet_start, now_local
from .scraper import BlockedError, Scraper
from .storage import Storage
from .telegram_bot import Telegram, esc, format_deal

log = logging.getLogger(__name__)

HELP = """<b>PS5-Deal-Scanner</b>
Ich durchsuche Kleinanzeigen alle paar Sekunden nach PS5-Angeboten (auch defekte) und melde nur lohnende Deals.

<b>Buttons</b>
✉️ Anschreiben – Nachricht an den Verkäufer (mit Vorschau)
📝 Text – Nachrichtenentwurf zum Kopieren
🙈 Ignorieren – Deal ausblenden

<b>Befehle</b>
/status – Laufzeit & Statistik
/pause · /weiter – Meldungen anhalten/fortsetzen
/preise – aktuelle Marktwerte
/top – letzte gemeldete Deals"""


def default_message(listing: Listing) -> str:
    text = "Hallo, ist die PlayStation noch zu haben?"
    if listing.negotiable and listing.price:
        offer = int(round(listing.price * 0.88 / 10) * 10)
        text += f" Würdest du sie für {offer} € abgeben?"
    text += " Ich könnte sie zeitnah abholen" + (" oder du schickst sie per Versand." if listing.shipping else ".")
    return text


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.storage = Storage(cfg.db_path)
        self.scraper = Scraper(cfg.max_concurrent_requests)
        self.market = Market(cfg, self.storage)
        self.ai = AIFilter(cfg) if cfg.ai_enabled else None
        self.telegram = Telegram(cfg.telegram_token, cfg.telegram_chat_id)
        self.messenger = Messenger(cfg)
        self.paused = self.storage.kv_get("paused") == "1"
        self.in_flight: set[str] = set()
        self.tasks: set[asyncio.Task] = set()
        self.started = time.time()
        self.scans = 0
        self.last_scan_ok = 0.0
        self.block_notified = False
        self.pending_custom: tuple[str, float] | None = None
        self.custom_texts: dict[str, str] = {}

    # ------------------------------------------------------------------ Lauf
    async def run(self) -> None:
        log.info("Starte Scanner: %d Suchen, Intervall %.0f s, KI %s, Telegram %s, Messenger %s",
                 len(self.cfg.queries), self.cfg.interval_seconds, "an" if self.ai else "aus",
                 "an" if self.telegram.enabled else "aus", "an" if self.messenger.available else "aus")
        await self.telegram.set_commands()
        await self.telegram.send_text(
            "🚀 <b>PS5-Scanner gestartet</b>\n"
            f"{len(self.cfg.queries)} Suchen · alle ~{self.cfg.interval_seconds:.0f} s · "
            f"KI {'an' if self.ai else 'aus'} · Messenger {'an' if self.messenger.available else 'aus'}\n"
            f"Meldungen {self.cfg.active_from:%H:%M}–{self.cfg.active_until:%H:%M} Uhr"
            + (" · ⏸ pausiert" if self.paused else "")
        )
        try:
            await asyncio.gather(self.scan_loop(), self.telegram.poll(self.on_update), self.housekeeping_loop())
        finally:
            await self.scraper.close()
            await self.telegram.close()
            await self.messenger.close()

    async def run_once(self) -> None:
        """Ein einzelner Durchlauf (zum Testen): sucht, bewertet und zeigt Ergebnisse im Log."""
        await self.scan_all()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.scraper.close()
        await self.telegram.close()

    async def scan_loop(self) -> None:
        while True:
            t0 = time.monotonic()
            await self.scan_all()
            elapsed = time.monotonic() - t0
            delay = max(self.cfg.interval_seconds + random.uniform(-self.cfg.jitter_seconds, self.cfg.jitter_seconds) - elapsed, 3)
            await asyncio.sleep(delay)

    async def scan_all(self) -> None:
        results = await asyncio.gather(*(self.scan_query(q.name, q.url) for q in self.cfg.queries), return_exceptions=True)
        self.scans += 1
        if self.scans % 30 == 0:
            log.info("Durchlauf %d – %d/%d Suchen ok", self.scans, sum(r is True for r in results), len(results))
        if any(r is True for r in results):
            self.last_scan_ok = time.time()
            self.block_notified = False
        elif self.last_scan_ok and time.time() - self.last_scan_ok > 900 and not self.block_notified:
            self.block_notified = True
            await self.telegram.send_text("⚠️ Seit 15 Minuten keine erfolgreiche Suche – evtl. von Kleinanzeigen "
                                          "blockiert. Ich versuche es weiter mit längeren Pausen.")

    async def scan_query(self, name: str, url: str) -> bool:
        try:
            listings = await self.scraper.search(url, name)
        except BlockedError as e:
            log.warning("[%s] %s", name, e)
            return False
        except Exception as e:
            log.warning("[%s] Suche fehlgeschlagen: %s", name, e)
            return False

        known = self.storage.known_ids([l.ad_id for l in listings])
        if not self.storage.query_initialized(name):
            if not self.cfg.notify_on_first_run:
                now = now_local(self.cfg)
                for l in listings:
                    if l.ad_id not in known:
                        a = analyze(l, self.cfg, self.market.resale_table(), now)
                        self.market.observe(l, a)
                        self.storage.upsert(l, "seen", model=a.model.value, score=a.score, reason="Erstlauf")
                self.storage.mark_query_initialized(name)
                log.info("[%s] Erstlauf: %d Inserate als bekannt markiert", name, len(listings))
                return True
            self.storage.mark_query_initialized(name)

        touched = []
        for l in listings:
            row = known.get(l.ad_id)
            if row is None:
                self._spawn(l, price_drop=False)
            else:
                touched.append(l.ad_id)
                old = row["price"]
                if (l.price is not None and old and l.price <= old * 0.9 and old - l.price >= 20
                        and row["status"] not in ("notified", "contacted", "ignored")):
                    l.old_price = old
                    self._spawn(l, price_drop=True)
        self.storage.touch(touched)
        log.debug("[%s] %d Treffer, %d neu", name, len(listings), len(listings) - len(touched))
        return True

    def _spawn(self, listing: Listing, price_drop: bool) -> None:
        if listing.ad_id in self.in_flight:
            return
        self.in_flight.add(listing.ad_id)
        task = asyncio.create_task(self.process(listing, price_drop))
        self.tasks.add(task)
        task.add_done_callback(lambda t: (self.tasks.discard(t), self.in_flight.discard(listing.ad_id)))

    # ------------------------------------------------------------- Pipeline
    async def process(self, listing: Listing, price_drop: bool) -> None:
        try:
            await self._process(listing, price_drop)
        except Exception:
            log.exception("Fehler bei Inserat %s", listing.ad_id)

    async def _process(self, listing: Listing, price_drop: bool) -> None:
        cfg = self.cfg
        now = now_local(cfg)
        resale = self.market.resale_table()
        analysis = analyze(listing, cfg, resale, now)
        self.market.observe(listing, analysis)

        if not analysis.relevant:
            self.storage.upsert(listing, "rejected", model=analysis.model.value, reason=analysis.reject_reason)
            log.debug("✗ %s – %s", listing.title[:60], analysis.reject_reason)
            return

        age = posted_minutes_ago(listing.posted, now)
        if not price_drop and (age is None or age > cfg.max_age_minutes):
            self.storage.upsert(listing, "seen", model=analysis.model.value, score=analysis.score,
                                reason="alt/Topanzeige")
            return
        if analysis.score < cfg.candidate_score:
            self.storage.upsert(listing, "seen", model=analysis.model.value, score=analysis.score, reason="Score zu niedrig")
            log.info("· %3.0f %s (%s €)", analysis.score, listing.title[:70], listing.price)
            return

        # Kandidat -> Detailseite laden und neu bewerten (genaue Beschreibung, Modell, Verkäufer)
        try:
            await self.scraper.enrich(listing)
        except Exception as e:
            log.warning("Detailseite %s nicht geladen: %s", listing.ad_id, e)
        if listing.gone:
            self.storage.upsert(listing, "seen", reason="nicht mehr verfügbar")
            return
        if listing.detail_loaded:
            analysis = analyze(listing, cfg, resale, now)
            if not analysis.relevant or analysis.score < cfg.candidate_score:
                self.storage.upsert(listing, "seen", model=analysis.model.value, score=analysis.score,
                                    reason=analysis.reject_reason or "Score nach Details zu niedrig")
                log.info("· %3.0f %s – nach Details verworfen", analysis.score, listing.title[:70])
                return

        log.info("★ %3.0f %s (%s €, Gewinn %s €)", analysis.score, listing.title[:70], listing.price, analysis.expected_profit)

        if not is_active(cfg, now) or self.paused:
            self.storage.upsert(listing, "digest", model=analysis.model.value, score=analysis.score,
                                extra={"analysis": asdict(analysis)})
            return

        header = "📉 <b>Preis gesenkt</b>" if price_drop else None
        await self.decide_and_notify(listing, analysis, header=header)

    def _rule_deal(self, listing: Listing, a: Analysis) -> bool:
        if a.deal_breakers or len(a.red_flags) >= 2:
            return False
        if a.expected_profit is None:
            return a.defective  # defekt ohne Preis -> Preisvorschlag lohnt sich evtl.
        return a.expected_profit >= self.cfg.min_profit

    async def decide_and_notify(self, listing: Listing, analysis: Analysis, header: str | None = None) -> bool:
        cfg = self.cfg
        instant = (
            analysis.score >= cfg.instant_score
            and analysis.console_confirmed
            and not analysis.red_flags
            and self._rule_deal(listing, analysis)
        )
        extra = {"analysis": asdict(analysis)}

        if self.ai is None:
            if not self._rule_deal(listing, analysis):
                self.storage.upsert(listing, "seen", model=analysis.model.value, score=analysis.score, reason="kein Gewinn")
                return False
            await self._notify(listing, analysis, None, header, extra)
            return True

        if instant:
            # Schnell zuerst: sofort melden, KI-Urteil wird nachgereicht (Nachricht wird bearbeitet).
            msg_id = await self._notify(listing, analysis, None, header, extra, ai_pending=True)
            verdict = await self.ai.evaluate(listing, analysis, self.market.resale_table())
            if verdict:
                extra["verdict"] = asdict(verdict)
                self.storage.upsert(listing, "notified", model=analysis.model.value, score=analysis.score, extra=extra)
                if msg_id:
                    self.storage.set_status(listing.ad_id, "notified", msg_id)
                    await self.telegram.edit_deal(msg_id, listing, format_deal(listing, analysis, verdict, header=header),
                                                  has_photo=bool(listing.image_urls or listing.image_url))
            return True

        verdict = await self.ai.evaluate(listing, analysis, self.market.resale_table())
        if verdict is None:
            # KI nicht verfügbar -> nach Regeln entscheiden
            if self._rule_deal(listing, analysis):
                await self._notify(listing, analysis, None, header, extra)
                return True
            self.storage.upsert(listing, "seen", model=analysis.model.value, score=analysis.score, reason="kein Gewinn")
            return False

        extra["verdict"] = asdict(verdict)
        if verdict.is_deal and verdict.confidence >= 0.5 and verdict.scam_risk != "hoch":
            await self._notify(listing, analysis, verdict, header, extra)
            return True
        self.storage.upsert(listing, "skipped_ai", model=analysis.model.value, score=analysis.score,
                            reason=verdict.reasoning, extra=extra)
        log.info("  KI: kein Deal – %s", verdict.reasoning)
        return False

    async def _notify(self, listing: Listing, analysis: Analysis, verdict: AIVerdict | None, header: str | None,
                      extra: dict, ai_pending: bool = False) -> int | None:
        caption = format_deal(listing, analysis, verdict, header=header, ai_pending=ai_pending)
        self.storage.upsert(listing, "notified", model=analysis.model.value, score=analysis.score, extra=extra)
        msg_id = await self.telegram.send_deal(listing, caption)
        if msg_id:
            self.storage.set_status(listing.ad_id, "notified", msg_id)
        log.info("📨 Gemeldet: %s", listing.title[:70])
        return msg_id

    # ------------------------------------------------------ Morgen-Überblick
    async def housekeeping_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            now = now_local(self.cfg)
            today = now.strftime("%Y-%m-%d")
            if is_active(self.cfg, now) and self.storage.kv_get("digest_date") != today:
                self.storage.kv_set("digest_date", today)
                if self.cfg.night_digest and not self.paused:
                    try:
                        await self.send_digest(now)
                    except Exception:
                        log.exception("Morgen-Überblick fehlgeschlagen")
                self.storage.cleanup()

    async def send_digest(self, now: datetime) -> None:
        since = last_quiet_start(self.cfg, now).timestamp() - 3600
        rows = self.storage.digest_candidates(since, self.cfg.digest_max * 3)
        sent = 0
        for row in rows:
            data = self.storage.load_data(row["ad_id"]) or {}
            self.storage.set_status(row["ad_id"], "seen")
            if sent >= self.cfg.digest_max or "listing" not in data:
                continue
            listing = Listing(**data["listing"])
            listing.gone, listing.image_urls = False, []
            try:
                await self.scraper.enrich(listing)  # noch online?
            except Exception:
                continue
            if listing.gone:
                continue
            analysis = analyze(listing, self.cfg, self.market.resale_table(), now)
            if not analysis.relevant:
                continue
            if sent == 0:
                await self.telegram.send_text("🌅 <b>Guten Morgen!</b> Diese Deals kamen über Nacht rein und sind noch online:")
            if await self.decide_and_notify(listing, analysis, header="🌙 <b>Über Nacht</b>"):
                sent += 1

    # -------------------------------------------------------------- Telegram
    async def on_update(self, update: dict) -> None:
        if "callback_query" in update:
            await self.on_callback(update["callback_query"])
            return
        message = update.get("message") or {}
        text = (message.get("text") or "").strip()
        if not text:
            return
        if text.startswith("/"):
            await self.on_command(text.split()[0].split("@")[0].lower())
            return
        if self.pending_custom and time.time() - self.pending_custom[1] < 600:
            ad_id = self.pending_custom[0]
            self.pending_custom = None
            self.custom_texts[ad_id] = text
            await self._send_preview(ad_id, text)

    async def on_command(self, cmd: str) -> None:
        if cmd in ("/start", "/hilfe", "/help"):
            await self.telegram.send_text(HELP)
        elif cmd == "/status":
            stats = self.storage.stats(time.time() - 86400)
            uptime = int((time.time() - self.started) / 60)
            last = int(time.time() - self.last_scan_ok) if self.last_scan_ok else None
            await self.telegram.send_text(
                f"📊 <b>Status</b>\nLaufzeit: {uptime // 60} h {uptime % 60} min · Durchläufe: {self.scans}\n"
                f"Letzte erfolgreiche Suche: {'vor ' + str(last) + ' s' if last is not None else '–'}\n"
                f"Meldungen: {'⏸ pausiert' if self.paused else ('aktiv' if is_active(self.cfg) else '🌙 Ruhezeit')}\n"
                f"KI: {'an (' + self.cfg.ai_model + ')' if self.ai else 'aus'} · Messenger: {'an' if self.messenger.available else 'aus'}\n\n"
                f"<b>Letzte 24 h</b>\n" + "\n".join(f"{k}: {v}" for k, v in sorted(stats.items()))
            )
        elif cmd == "/pause":
            self.paused = True
            self.storage.kv_set("paused", "1")
            await self.telegram.send_text("⏸ Meldungen pausiert. Mit /weiter fortsetzen.")
        elif cmd in ("/weiter", "/resume"):
            self.paused = False
            self.storage.kv_set("paused", "0")
            await self.telegram.send_text("▶️ Meldungen laufen wieder.")
        elif cmd == "/preise":
            await self.telegram.send_text("💶 <b>Wiederverkaufswerte</b>\n" + self.market.describe())
        elif cmd == "/top":
            rows = self.storage.recent_notified(10)
            if not rows:
                await self.telegram.send_text("Noch keine Deals gemeldet.")
                return
            lines = [f"• <a href=\"{r['url']}\">{esc(r['title'][:60])}</a> – {r['price'] if r['price'] is not None else '?'} € "
                     f"(Score {r['score']:.0f}{', ✉️' if r['status'] == 'contacted' else ''})" for r in rows]
            await self.telegram.send_text("🏆 <b>Letzte Deals</b>\n" + "\n".join(lines))

    def _draft(self, ad_id: str) -> tuple[Listing | None, str]:
        data = self.storage.load_data(ad_id)
        if not data or "listing" not in data:
            return None, ""
        listing = Listing(**data["listing"])
        draft = self.custom_texts.get(ad_id) or (data.get("verdict") or {}).get("message_draft") or default_message(listing)
        if self.cfg.messenger_signature and ad_id not in self.custom_texts:
            draft = f"{draft}\n\n{self.cfg.messenger_signature}"
        return listing, draft

    async def _send_preview(self, ad_id: str, text: str) -> None:
        await self.telegram.send_text(
            f"✉️ <b>Vorschau</b>\n\n{esc(text)}",
            reply_markup={"inline_keyboard": [[
                {"text": "✅ Jetzt senden", "callback_data": f"s:{ad_id}"},
                {"text": "✏️ Eigener Text", "callback_data": f"e:{ad_id}"},
                {"text": "❌", "callback_data": "x:"},
            ]]},
        )

    async def on_callback(self, cq: dict) -> None:
        action, _, ad_id = (cq.get("data") or "").partition(":")
        cq_id = cq["id"]
        msg = cq.get("message") or {}

        if action == "x":
            await self.telegram.answer_callback(cq_id, "Abgebrochen")
            await self.telegram.call("deleteMessage", chat_id=self.telegram.chat_id, message_id=msg.get("message_id"))
            return
        if action == "i":
            self.storage.set_status(ad_id, "ignored")
            await self.telegram.answer_callback(cq_id, "Ignoriert 🙈")
            await self.telegram.call("deleteMessage", chat_id=self.telegram.chat_id, message_id=msg.get("message_id"))
            return

        listing, draft = self._draft(ad_id)
        if listing is None:
            await self.telegram.answer_callback(cq_id, "Inserat nicht mehr in der Datenbank")
            return

        if action == "t":
            await self.telegram.answer_callback(cq_id)
            await self.telegram.send_text(f"📝 Entwurf für <a href=\"{listing.url}\">{esc(listing.title[:50])}</a>:\n\n"
                                          f"<code>{esc(draft)}</code>")
        elif action == "c":
            await self.telegram.answer_callback(cq_id)
            if not self.messenger.available:
                await self.telegram.send_text(
                    "ℹ️ Automatisches Anschreiben ist noch nicht eingerichtet (siehe README → Messenger).\n"
                    f"Text zum Kopieren:\n\n<code>{esc(draft)}</code>",
                    reply_markup={"inline_keyboard": [[{"text": "🔗 Inserat öffnen", "url": listing.url}]]},
                )
                return
            if self.storage.already_contacted(ad_id):
                await self.telegram.send_text("ℹ️ Diesen Verkäufer hast du bereits angeschrieben.")
            await self._send_preview(ad_id, draft)
        elif action == "e":
            self.pending_custom = (ad_id, time.time())
            await self.telegram.answer_callback(cq_id)
            await self.telegram.send_text("✏️ Schick mir jetzt deinen Text als normale Nachricht – ich zeige dir danach die Vorschau.")
        elif action == "s":
            hour_ago = time.time() - 3600
            if self.storage.contacts_since(hour_ago) >= self.cfg.messenger_max_per_hour:
                await self.telegram.answer_callback(cq_id, "Stundenlimit erreicht – bitte später erneut")
                return
            await self.telegram.answer_callback(cq_id, "Sende …")
            try:
                result = await self.messenger.send(listing.url, draft)
                self.storage.log_contact(ad_id, draft, True, result)
                self.storage.set_status(ad_id, "contacted")
                self.custom_texts.pop(ad_id, None)
                await self.telegram.call("editMessageText", chat_id=self.telegram.chat_id, message_id=msg.get("message_id"),
                                         text=f"✅ <b>Gesendet</b> an <a href=\"{listing.url}\">{esc(listing.title[:60])}</a>\n\n{esc(draft)}",
                                         parse_mode="HTML", link_preview_options={"is_disabled": True})
            except MessengerError as e:
                self.storage.log_contact(ad_id, draft, False, str(e))
                await self.telegram.send_text(f"❌ Senden fehlgeschlagen: {esc(str(e))}\n\nText zum Kopieren:\n<code>{esc(draft)}</code>",
                                              reply_markup={"inline_keyboard": [[{"text": "🔗 Inserat öffnen", "url": listing.url}]]})
