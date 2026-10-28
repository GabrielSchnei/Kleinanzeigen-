"""Ruhezeiten: Benachrichtigungen nur zwischen active_from und active_until (Standard 06:00–23:00)."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .config import Config


def now_local(cfg: Config) -> datetime:
    """Aktuelle Ortszeit als naive datetime (passt zu den Zeitangaben von Kleinanzeigen)."""
    return datetime.now(ZoneInfo(cfg.timezone)).replace(tzinfo=None)


def is_active(cfg: Config, now: datetime | None = None) -> bool:
    now = now or now_local(cfg)
    t: time = now.time()
    start, end = cfg.active_from, cfg.active_until
    if start <= end:
        return start <= t < end
    return t >= start or t < end  # Zeitraum über Mitternacht


def last_quiet_start(cfg: Config, now: datetime | None = None) -> datetime:
    """Beginn der letzten Ruhephase (für den Morgen-Überblick)."""
    now = now or now_local(cfg)
    candidate = now.replace(hour=cfg.active_until.hour, minute=cfg.active_until.minute, second=0, microsecond=0)
    if candidate > now:
        candidate -= timedelta(days=1)
    return candidate
