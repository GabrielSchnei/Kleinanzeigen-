from datetime import datetime, time

from ps5_scanner.schedule import is_active, last_quiet_start


def test_active_hours(cfg):
    assert not is_active(cfg, datetime(2026, 10, 28, 5, 59))
    assert is_active(cfg, datetime(2026, 10, 28, 6, 0))
    assert is_active(cfg, datetime(2026, 10, 28, 22, 59))
    assert not is_active(cfg, datetime(2026, 10, 28, 23, 0))
    assert not is_active(cfg, datetime(2026, 10, 28, 2, 0))


def test_wraparound(cfg):
    cfg.active_from, cfg.active_until = time(20, 0), time(2, 0)
    assert is_active(cfg, datetime(2026, 10, 28, 1, 0))
    assert not is_active(cfg, datetime(2026, 10, 28, 12, 0))


def test_last_quiet_start(cfg):
    assert last_quiet_start(cfg, datetime(2026, 10, 28, 6, 5)) == datetime(2026, 10, 27, 23, 0)
