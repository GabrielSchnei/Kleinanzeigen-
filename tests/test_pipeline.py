import asyncio
from datetime import datetime

from ps5_scanner import app as app_module
from ps5_scanner.app import App


def _run(cfg, listing, now, monkeypatch):
    app = App(cfg)
    sent = []

    async def fake_enrich(l):
        l.detail_loaded = True
        return l

    async def fake_send(l, caption):
        sent.append(caption)
        return 42

    monkeypatch.setattr(app.scraper, "enrich", fake_enrich)
    monkeypatch.setattr(app.telegram, "send_deal", fake_send)
    monkeypatch.setattr(app_module, "now_local", lambda cfg: now)

    async def go():
        await app.process(listing, price_drop=False)
        await app.scraper.close()
        await app.telegram.close()

    asyncio.run(go())
    return app, sent


def test_deal_is_notified_during_day(cfg, make_listing, monkeypatch):
    listing = make_listing("PS5 Disc Edition HDMI defekt", 150, "Kein Bild, HDMI Buchse kaputt.", negotiable=True)
    listing.posted = "Heute, 10:00"
    app, sent = _run(cfg, listing, datetime(2026, 10, 28, 10, 5), monkeypatch)
    assert len(sent) == 1 and "HDMI" in sent[0]
    row = app.storage.get(listing.ad_id)
    assert row["status"] == "notified" and row["message_id"] == 42


def test_deal_goes_to_digest_at_night(cfg, make_listing, monkeypatch):
    listing = make_listing("PS5 Disc Edition HDMI defekt", 150, "Kein Bild, HDMI Buchse kaputt.")
    listing.posted = "Heute, 02:00"
    app, sent = _run(cfg, listing, datetime(2026, 10, 28, 2, 3), monkeypatch)
    assert sent == []
    assert app.storage.get(listing.ad_id)["status"] == "digest"


def test_old_listing_is_ignored(cfg, make_listing, monkeypatch):
    listing = make_listing("PS5 Disc Edition HDMI defekt", 150, "Kein Bild.")
    listing.posted = "Gestern, 10:00"
    app, sent = _run(cfg, listing, datetime(2026, 10, 28, 10, 5), monkeypatch)
    assert sent == []
    assert app.storage.get(listing.ad_id)["status"] == "seen"
