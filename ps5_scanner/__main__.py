"""Kommandozeile.

    python -m ps5_scanner              # Scanner starten (Dauerbetrieb)
    python -m ps5_scanner once         # ein Durchlauf, Ergebnisse im Terminal
    python -m ps5_scanner check URL    # einzelnes Inserat analysieren (inkl. KI)
    python -m ps5_scanner test         # Telegram-Testnachricht
    python -m ps5_scanner login        # Kleinanzeigen-Login für das automatische Anschreiben speichern
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import asdict

from .config import load_config


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def check_url(cfg, url: str) -> None:
    from .ai_filter import AIFilter
    from .analyzer import analyze
    from .market import Market
    from .models import Listing
    from .scraper import Scraper
    from .storage import Storage
    from .telegram_bot import format_deal

    m = __import__("re").search(r"/(\d{8,})-", url)
    listing = Listing(ad_id=m.group(1) if m else "0", url=url, title="", price=None, negotiable=False,
                      location="", posted="", snippet="")
    scraper = Scraper()
    try:
        await scraper.enrich(listing)
    finally:
        await scraper.close()
    if listing.gone:
        print("Inserat nicht (mehr) verfügbar.")
        return
    market = Market(cfg, Storage(cfg.db_path))
    analysis = analyze(listing, cfg, market.resale_table())
    verdict = None
    if cfg.ai_enabled:
        verdict = await AIFilter(cfg).evaluate(listing, analysis, market.resale_table())
    print(format_deal(listing, analysis, verdict).replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", ""))
    print("\nAnalyse:", {k: v for k, v in asdict(analysis).items() if k not in ("defects",)})
    if verdict:
        print("\nNachrichtenentwurf:\n" + verdict.message_draft)


def main() -> None:
    ap = argparse.ArgumentParser(prog="ps5_scanner", description="PS5-Deal-Scanner für Kleinanzeigen")
    ap.add_argument("command", nargs="?", default="run", choices=["run", "once", "check", "test", "login"])
    ap.add_argument("url", nargs="?")
    ap.add_argument("-c", "--config", default="config.yaml")
    args = ap.parse_args()

    cfg = load_config(args.config)
    setup_logging(cfg.log_level)

    if args.command == "login":
        from .messenger import interactive_login
        asyncio.run(interactive_login(cfg))
        return

    if args.command == "test":
        from .telegram_bot import Telegram

        async def _test():
            tg = Telegram(cfg.telegram_token, cfg.telegram_chat_id)
            if not tg.enabled:
                sys.exit("TELEGRAM_BOT_TOKEN und TELEGRAM_CHAT_ID in .env setzen.")
            ok = await tg.send_text("✅ Testnachricht vom PS5-Scanner – Telegram funktioniert!")
            await tg.close()
            print("Gesendet." if ok else "Fehler – Token/Chat-ID prüfen (hast du dem Bot schon /start geschrieben?).")
        asyncio.run(_test())
        return

    if args.command == "check":
        if not args.url:
            sys.exit("Bitte URL angeben: python -m ps5_scanner check https://www.kleinanzeigen.de/s-anzeige/...")
        asyncio.run(check_url(cfg, args.url))
        return

    from .app import App

    app = App(cfg)
    if args.command == "once":
        cfg.notify_on_first_run = True
        cfg.max_age_minutes = 24 * 60
        asyncio.run(app.run_once())
        return
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        print("\nBeendet.")


if __name__ == "__main__":
    main()
