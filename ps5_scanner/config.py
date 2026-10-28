"""Konfiguration aus config.yaml + Geheimnisse aus Umgebungsvariablen / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

import yaml

DEFAULT_QUERIES = [
    # Kategorie "Konsolen" > PlayStation, nur Angebote, sortiert nach neuesten (Standard).
    {"name": "ps5", "url": "https://www.kleinanzeigen.de/s-konsolen/playstation/anzeige:angebote/ps5/k0c279+konsolen.art_s:playstation"},
    {"name": "playstation 5", "url": "https://www.kleinanzeigen.de/s-konsolen/playstation/anzeige:angebote/playstation-5/k0c279+konsolen.art_s:playstation"},
    # Zustand "Defekt" explizit – hier liegen die Reparatur-Schnäppchen.
    {"name": "defekt", "url": "https://www.kleinanzeigen.de/s-konsolen/playstation/anzeige:angebote/ps5/k0c279+konsolen.art_s:playstation+global.zustand:defect"},
    # Manche stellen defekte Konsolen außerhalb der Kategorie ein.
    {"name": "ps5 defekt (alle Kat.)", "url": "https://www.kleinanzeigen.de/s-anzeige:angebote/ps5-defekt/k0"},
]

DEFAULT_RESALE = {
    # Realistische Verkaufspreise (funktionsfähig, gebraucht, mit 1 Controller).
    # Werden bei aktivem `dynamic_market` laufend anhand echter Inserate nachjustiert.
    "disc": 430,
    "digital": 380,
    "slim_disc": 430,
    "slim_digital": 380,
    "pro": 1000,
    "unknown": 380,
}

DEFAULT_REPAIR = {
    # key: [Teilekosten €, Schwierigkeit, kann ich selbst?]
    "software": [0, "leicht", True],      # abgesicherter Modus, Firmware neu aufspielen
    "thermal": [15, "leicht", True],      # Wärmeleitpaste/Flüssigmetall, Reinigung
    "fan": [20, "leicht", True],          # Lüfter tauschen/reinigen
    "hdmi": [12, "mittel", True],         # HDMI-Buchse löten (+ evtl. Retimer-Chip)
    "psu": [45, "mittel", True],          # Netzteil tauschen
    "usb": [8, "mittel", True],
    "drive": [45, "mittel", False],       # Laufwerk ist ans Board gekoppelt -> nur Laser/Mechanik
    "controller_missing": [45, "leicht", True],  # Controller nachkaufen
    "unknown_defect": [40, "mittel", False],
}


@dataclass
class Query:
    name: str
    url: str


@dataclass
class Config:
    # Scan
    queries: list[Query]
    interval_seconds: float = 20
    jitter_seconds: float = 5
    notify_on_first_run: bool = False
    max_age_minutes: int = 45
    max_concurrent_requests: int = 4

    # Ruhezeiten
    timezone: str = "Europe/Berlin"
    active_from: time = time(6, 0)
    active_until: time = time(23, 0)
    night_digest: bool = True
    digest_max: int = 5

    # Bewertung
    resale: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_RESALE))
    dynamic_market: bool = True
    repair: dict[str, list] = field(default_factory=lambda: {k: list(v) for k, v in DEFAULT_REPAIR.items()})
    min_profit: int = 60
    min_price: int = 40
    max_price: int = 1100
    instant_score: float = 80
    candidate_score: float = 40
    selling_overhead: int = 15            # Versand/Fahrt/Gebühren pro Deal
    exclude_pro_sellers: bool = True

    # KI
    ai_enabled: bool = True
    ai_model: str = "claude-opus-5-5"
    ai_effort: str = "low"
    ai_use_images: bool = True
    ai_max_images: int = 3
    ai_timeout_seconds: float = 45

    # Messenger
    messenger_enabled: bool = False
    messenger_headless: bool = True
    messenger_storage_state: str = "data/kleinanzeigen_state.json"
    messenger_contact_name: str = ""
    messenger_signature: str = ""
    messenger_max_per_hour: int = 15

    # Telegram / Geheimnisse
    telegram_token: str = ""
    telegram_chat_id: str = ""
    anthropic_api_key: str = ""

    # Pfade
    db_path: str = "data/scanner.db"
    log_level: str = "INFO"


def _parse_time(value: str) -> time:
    h, m = str(value).split(":")
    return time(int(h), int(m))


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_config(path: str | os.PathLike = "config.yaml") -> Config:
    path = Path(path)
    _load_dotenv(path.parent / ".env")
    raw: dict = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    scan = raw.get("scan", {})
    quiet = raw.get("quiet_hours", {})
    deal = raw.get("deal", {})
    ai = raw.get("ai", {})
    msg = raw.get("messenger", {})

    queries = [Query(q["name"], q["url"]) for q in scan.get("queries", DEFAULT_QUERIES)]

    resale = dict(DEFAULT_RESALE)
    resale.update(deal.get("resale", {}))
    repair = {k: list(v) for k, v in DEFAULT_REPAIR.items()}
    for key, val in (deal.get("repair", {}) or {}).items():
        repair[key] = list(val)
    for key in deal.get("my_skills", []) or []:
        if key in repair:
            repair[key][2] = True

    cfg = Config(
        queries=queries,
        interval_seconds=float(scan.get("interval_seconds", 20)),
        jitter_seconds=float(scan.get("jitter_seconds", 5)),
        notify_on_first_run=bool(scan.get("notify_on_first_run", False)),
        max_age_minutes=int(scan.get("max_age_minutes", 45)),
        max_concurrent_requests=int(scan.get("max_concurrent_requests", 4)),
        timezone=quiet.get("timezone", "Europe/Berlin"),
        active_from=_parse_time(quiet.get("active_from", "06:00")),
        active_until=_parse_time(quiet.get("active_until", "23:00")),
        night_digest=bool(quiet.get("night_digest", True)),
        digest_max=int(quiet.get("digest_max", 5)),
        resale=resale,
        dynamic_market=bool(deal.get("dynamic_market", True)),
        repair=repair,
        min_profit=int(deal.get("min_profit", 60)),
        min_price=int(deal.get("min_price", 40)),
        max_price=int(deal.get("max_price", 1100)),
        instant_score=float(deal.get("instant_score", 80)),
        candidate_score=float(deal.get("candidate_score", 40)),
        selling_overhead=int(deal.get("selling_overhead", 15)),
        exclude_pro_sellers=bool(deal.get("exclude_pro_sellers", True)),
        ai_enabled=bool(ai.get("enabled", True)),
        ai_model=ai.get("model", "claude-opus-5-5"),
        ai_effort=ai.get("effort", "low"),
        ai_use_images=bool(ai.get("use_images", True)),
        ai_max_images=int(ai.get("max_images", 3)),
        ai_timeout_seconds=float(ai.get("timeout_seconds", 45)),
        messenger_enabled=bool(msg.get("enabled", False)),
        messenger_headless=bool(msg.get("headless", True)),
        messenger_storage_state=msg.get("storage_state", "data/kleinanzeigen_state.json"),
        messenger_contact_name=msg.get("contact_name", ""),
        messenger_signature=msg.get("signature", ""),
        messenger_max_per_hour=int(msg.get("max_per_hour", 15)),
        telegram_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        db_path=raw.get("db_path", "data/scanner.db"),
        log_level=raw.get("log_level", "INFO"),
    )
    if not cfg.anthropic_api_key:
        cfg.ai_enabled = False
    return cfg
