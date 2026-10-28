from pathlib import Path

from ps5_scanner.parser import parse_price, parse_search_page

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_price():
    assert parse_price("1.159 € VB") == (1159, True)
    assert parse_price("370 €") == (370, False)
    assert parse_price("VB") == (None, True)
    assert parse_price("Zu verschenken") == (0, False)


def test_parse_search_page():
    listings = parse_search_page((FIXTURES / "search.html").read_text(encoding="utf-8"), "ps5")
    assert [l.ad_id for l in listings] == ["111", "222", "333"]
    hdmi, wanted, shop = listings
    assert hdmi.title == "PS5 Disc Edition HDMI defekt"
    assert (hdmi.price, hdmi.negotiable, hdmi.old_price) == (180, True, 220)
    assert hdmi.location == "10115 Mitte"
    assert hdmi.posted == "Heute, 21:05"
    assert hdmi.shipping is True
    assert hdmi.url.startswith("https://www.kleinanzeigen.de/s-anzeige/")
    assert "rule=$_59" in hdmi.image_url
    assert wanted.is_wanted and wanted.price is None
    assert shop.is_pro_seller and shop.price == 1059 and shop.posted == ""
