"""Telegram-Anbindung über die Bot-API (ohne Zusatzbibliothek): Deals senden, Buttons, Befehle."""

from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Awaitable, Callable

import httpx

from .models import AIVerdict, Analysis, Listing

log = logging.getLogger(__name__)

CAPTION_LIMIT = 1024


def esc(text: str | None) -> str:
    return html.escape(text or "", quote=False)


def format_deal(listing: Listing, analysis: Analysis, verdict: AIVerdict | None, *, header: str | None = None,
                ai_pending: bool = False) -> str:
    if header is None:
        if verdict and verdict.is_deal and verdict.confidence >= 0.75:
            header = "🔥 <b>TOP-DEAL</b>"
        elif analysis.defective:
            header = "🔧 <b>Reparatur-Deal</b>"
        else:
            header = "💰 <b>Deal</b>"
    lines = [f"{header} · Score {analysis.score:.0f}", f"<b>{esc(listing.title)}</b>"]

    price = f"<b>{listing.price} €</b>" if listing.price is not None else "<b>Preis offen</b>"
    if listing.negotiable:
        price += " VB"
    if listing.old_price and listing.price is not None and listing.old_price > listing.price:
        price += f" (statt {listing.old_price} €)"
    meta = [price, f"📍 {esc(listing.location)}"]
    if listing.posted:
        meta.append(f"🕒 {esc(listing.posted)}")
    lines.append(" · ".join(meta))

    info = [f"🎮 {analysis.model.label}"]
    if listing.shipping:
        info.append("📦 Versand")
    if listing.seller_type:
        info.append(f"👤 {listing.seller_type}")
    lines.append(" · ".join(info))

    if analysis.defective:
        lines.append("🔧 " + esc("; ".join(d.label for d in analysis.defects)))
    money = []
    if analysis.resale_value:
        money.append(f"Wert ~{analysis.resale_value} €")
    if analysis.repair_cost:
        money.append(f"Teile ~{analysis.repair_cost} €")
    if analysis.expected_profit is not None:
        money.append(f"<b>Gewinn ~{analysis.expected_profit} €</b>")
    if money:
        lines.append("📈 " + " · ".join(money))
    if analysis.bonuses:
        lines.append("✅ " + esc(", ".join(analysis.bonuses)))
    if analysis.deal_breakers:
        lines.append("⛔ " + esc(", ".join(analysis.deal_breakers)))
    if analysis.red_flags:
        lines.append("⚠️ " + esc(", ".join(analysis.red_flags)))

    if verdict:
        icon = "✅" if verdict.is_deal else "❌"
        ai = f"🤖 {icon} KI {int(verdict.confidence * 100)}% · Betrugsrisiko {esc(verdict.scam_risk)}"
        lines.append(ai)
        if verdict.likely_fault and verdict.likely_fault.lower() not in {"keiner", "kein", "keine"}:
            lines.append(f"🩺 {esc(verdict.likely_fault)}" + (" (machbar)" if verdict.repairable_by_user else " (schwierig)"))
        offer = []
        if verdict.suggested_offer:
            offer.append(f"Angebot {verdict.suggested_offer} €")
        if verdict.max_buy_price:
            offer.append(f"max. {verdict.max_buy_price} €")
        if verdict.estimated_resale:
            offer.append(f"VK ~{verdict.estimated_resale} €")
        if offer:
            lines.append("🎯 " + " · ".join(offer))
        lines.append(f"<i>{esc(verdict.reasoning)}</i>")
    elif ai_pending:
        lines.append("🤖 <i>KI prüft …</i>")

    text = "\n".join(lines)
    if len(text) > CAPTION_LIMIT:
        text = text[: CAPTION_LIMIT - 1] + "…"
    return text


def deal_keyboard(listing: Listing) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "✉️ Anschreiben", "callback_data": f"c:{listing.ad_id}"},
                {"text": "📝 Text", "callback_data": f"t:{listing.ad_id}"},
            ],
            [
                {"text": "🔗 Öffnen", "url": listing.url},
                {"text": "🙈 Ignorieren", "callback_data": f"i:{listing.ad_id}"},
            ],
        ]
    }


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = str(chat_id)
        self.api = f"https://api.telegram.org/bot{token}"
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(65.0, connect=10.0))
        self.offset = 0

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    async def close(self) -> None:
        await self.client.aclose()

    async def call(self, method: str, **params) -> dict | None:
        if not self.enabled:
            log.info("[Telegram deaktiviert] %s %s", method, str(params.get("text") or params.get("caption") or "")[:200])
            return None
        for attempt in range(3):
            try:
                resp = await self.client.post(f"{self.api}/{method}", json=params)
                data = resp.json()
            except (httpx.HTTPError, ValueError) as e:
                log.warning("Telegram %s fehlgeschlagen (%s), Versuch %d", method, e, attempt + 1)
                await asyncio.sleep(1 + attempt)
                continue
            if data.get("ok"):
                return data.get("result")
            retry_after = data.get("parameters", {}).get("retry_after")
            if retry_after:
                await asyncio.sleep(retry_after)
                continue
            log.warning("Telegram %s: %s", method, data.get("description"))
            return None
        return None

    async def send_text(self, text: str, reply_markup: dict | None = None, disable_preview: bool = True) -> int | None:
        params = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                  "link_preview_options": {"is_disabled": disable_preview}}
        if reply_markup:
            params["reply_markup"] = reply_markup
        result = await self.call("sendMessage", **params)
        return result.get("message_id") if result else None

    async def send_deal(self, listing: Listing, caption: str) -> int | None:
        image = listing.image_urls[0] if listing.image_urls else listing.image_url
        markup = deal_keyboard(listing)
        if image:
            result = await self.call("sendPhoto", chat_id=self.chat_id, photo=image, caption=caption,
                                     parse_mode="HTML", reply_markup=markup)
            if result:
                return result.get("message_id")
        return await self.send_text(caption, reply_markup=markup)

    async def edit_deal(self, message_id: int, listing: Listing, caption: str, has_photo: bool = True) -> None:
        markup = deal_keyboard(listing)
        if has_photo:
            ok = await self.call("editMessageCaption", chat_id=self.chat_id, message_id=message_id, caption=caption,
                                 parse_mode="HTML", reply_markup=markup)
            if ok:
                return
        await self.call("editMessageText", chat_id=self.chat_id, message_id=message_id, text=caption,
                        parse_mode="HTML", reply_markup=markup, link_preview_options={"is_disabled": True})

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        await self.call("answerCallbackQuery", callback_query_id=callback_id, text=text[:200])

    async def set_commands(self) -> None:
        await self.call("setMyCommands", commands=[
            {"command": "status", "description": "Status & Statistik"},
            {"command": "pause", "description": "Benachrichtigungen pausieren"},
            {"command": "weiter", "description": "Benachrichtigungen fortsetzen"},
            {"command": "preise", "description": "Aktuelle Marktwerte"},
            {"command": "top", "description": "Letzte Deals"},
            {"command": "hilfe", "description": "Hilfe"},
        ])

    async def poll(self, on_update: Callable[[dict], Awaitable[None]]) -> None:
        """Long-Polling-Schleife für Button-Klicks und Befehle."""
        if not self.enabled:
            return
        while True:
            try:
                resp = await self.client.post(f"{self.api}/getUpdates", json={
                    "offset": self.offset, "timeout": 50, "allowed_updates": ["message", "callback_query"]})
                data = resp.json()
            except (httpx.HTTPError, ValueError) as e:
                log.debug("getUpdates Fehler: %s", e)
                await asyncio.sleep(3)
                continue
            if not data.get("ok"):
                log.warning("getUpdates: %s", data.get("description"))
                await asyncio.sleep(5)
                continue
            for update in data.get("result", []):
                self.offset = update["update_id"] + 1
                chat = (update.get("message") or update.get("callback_query", {}).get("message") or {}).get("chat", {})
                if str(chat.get("id")) != self.chat_id:
                    continue  # nur der eigene Chat darf den Bot steuern
                try:
                    await on_update(update)
                except Exception:
                    log.exception("Fehler bei Telegram-Update")
