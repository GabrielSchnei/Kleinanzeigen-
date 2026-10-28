from datetime import datetime

import pytest

from ps5_scanner.analyzer import analyze, detect_model, posted_minutes_ago
from ps5_scanner.models import Model


@pytest.mark.parametrize("title,price,reason_part", [
    ("PS5 Controller DualSense weiß", 45, "Zubehör"),
    ("Suche PS5 defekt", 200, "Suche"),
    ("Tausche PS5 gegen Gaming PC", None, "Tausch"),
    ("Spider-Man 2 PS5", 30, "Zubehör"),
    ("PlayStation Portal Remote Player PS5", 199, "Zubehör"),
    ("Xbox Series X", 300, "Keine PS5"),
    ("PS5 Slim Konsole", 20, "Preis unter"),
])
def test_rejects(cfg, make_listing, title, price, reason_part):
    a = analyze(make_listing(title, price), cfg, cfg.resale)
    assert not a.relevant
    assert reason_part in a.reject_reason


@pytest.mark.parametrize("text,expected", [
    ("PS5 Slim Digital Edition 1TB", Model.SLIM_DIGITAL),
    ("PS5 Slim mit Laufwerk", Model.SLIM_DISC),
    ("PlayStation 5 Pro 2TB", Model.PRO),
    ("Sony PS5 Disc Edition 825GB", Model.DISC),
    ("PS5 Digital Edition", Model.DIGITAL),
    ("PlayStation 5 CFI-1216A", Model.DISC),
    ("PlayStation 5 CFI-1116B", Model.DIGITAL),
    ("PlayStation 5 CFI-2016 Digital", Model.SLIM_DIGITAL),
    ("PS5 Konsole", Model.UNKNOWN),
])
def test_detect_model(make_listing, text, expected):
    assert detect_model(make_listing(text)) is expected


def test_hdmi_repair_deal(cfg, make_listing):
    l = make_listing("PS5 Disc Edition HDMI defekt", 180, "Konsole startet, aber kein Bild. HDMI Buchse kaputt. Mit Controller.",
                     negotiable=True)
    a = analyze(l, cfg, cfg.resale)
    assert a.relevant and a.defective
    assert [d.key for d in a.defects] == ["hdmi"]
    assert all(d.in_skillset for d in a.defects)
    assert a.expected_profit == cfg.resale["disc"] - 180 - cfg.repair["hdmi"][0] - cfg.selling_overhead
    assert a.score >= cfg.candidate_score


def test_psu_and_thermal(cfg, make_listing):
    a = analyze(make_listing("PS5 Slim geht nicht mehr an", 150, "Die Konsole lässt sich nicht einschalten."), cfg, cfg.resale)
    assert "psu" in [d.key for d in a.defects]
    a = analyze(make_listing("PS5 Digital überhitzt", 200, "Schaltet sich beim Spielen einfach aus."), cfg, cfg.resale)
    assert "thermal" in [d.key for d in a.defects]


def test_negation_is_not_defect(cfg, make_listing):
    a = analyze(make_listing("PS5 Disc Edition", 350, "Kein Defekt, funktioniert einwandfrei. Nichtraucherhaushalt."), cfg, cfg.resale)
    assert not a.defective
    assert not a.deal_breakers


def test_deal_breaker(cfg, make_listing):
    a = analyze(make_listing("PS5 defekt Ersatzteilspender", 120, "Wasserschaden, Mainboard defekt."), cfg, cfg.resale)
    assert a.deal_breakers
    assert a.score < cfg.candidate_score


def test_scam_flags(cfg, make_listing):
    a = analyze(make_listing("PS5 Pro neu OVP", 250, "Nur Versand, Zahlung per PayPal Freunde. Schreib mir auf WhatsApp."),
                cfg, cfg.resale)
    assert "Nur Vorkasse/PayPal Freunde" in a.red_flags
    assert "Unrealistisch günstig für funktionierende Konsole" in a.red_flags
    assert a.score < cfg.candidate_score


def test_good_working_deal(cfg, make_listing):
    a = analyze(make_listing("PS5 Slim Disc mit 2 Controller und OVP", 280, "Funktioniert einwandfrei."), cfg, cfg.resale)
    assert not a.defective
    assert "2+ Controller" in a.bonuses and "OVP" in a.bonuses
    assert a.expected_profit >= cfg.min_profit
    assert a.score >= cfg.instant_score


def test_posted_minutes_ago():
    now = datetime(2026, 10, 28, 12, 30)
    assert posted_minutes_ago("Heute, 12:00", now) == 30
    assert posted_minutes_ago("Gestern, 12:30", now) == 24 * 60
    assert posted_minutes_ago("", now) is None
