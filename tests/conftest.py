import pytest

from ps5_scanner.config import load_config
from ps5_scanner.models import Listing


@pytest.fixture
def cfg(tmp_path):
    c = load_config(tmp_path / "config.yaml")
    c.db_path = str(tmp_path / "test.db")
    c.ai_enabled = False
    return c


@pytest.fixture
def make_listing():
    def _make(title, price=None, description="", negotiable=False, **kw):
        return Listing(ad_id=kw.pop("ad_id", "1"), url="https://www.kleinanzeigen.de/s-anzeige/x/1-279-1", title=title,
                       price=price, negotiable=negotiable, location="12345 Musterstadt", posted="Heute, 10:00",
                       snippet=description[:100], description=description or None, **kw)
    return _make
