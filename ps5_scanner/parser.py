"""HTML-Parser für Kleinanzeigen-Trefferlisten und Detailseiten.

Kleinanzeigen nutzt für die Trefferliste generierte Tailwind-Klassen, die sich ändern können.
Deshalb orientiert sich der Parser an stabilen Merkmalen (data-adid, h3, Textmuster, line-through)
statt an konkreten Klassennamen.
"""

from __future__ import annotations

import html as html_lib
import json
import re

from selectolax.lexbor import LexborHTMLParser as HTMLParser, LexborNode as Node

from .models import Listing

BASE_URL = "https://www.kleinanzeigen.de"

_DATE_RE = re.compile(r"^(Heute|Gestern),\s*\d{1,2}:\d{2}$|^\d{2}\.\d{2}\.\d{4}$")
_PRICE_RE = re.compile(r"([\d.]+)\s*€")
_ZIP_RE = re.compile(r"^\d{5}\b")


def parse_price(text: str) -> tuple[int | None, bool]:
    """'1.159 € VB' -> (1159, True); 'VB' -> (None, True); 'Zu verschenken' -> (0, False)."""
    text = (text or "").strip()
    negotiable = "VB" in text
    if "verschenken" in text.lower():
        return 0, False
    m = _PRICE_RE.search(text)
    if not m:
        return None, negotiable
    return int(m.group(1).replace(".", "")), negotiable


def _text(node: Node | None) -> str:
    return node.text(strip=True) if node is not None else ""


def parse_search_page(page_html: str, source_query: str = "") -> list[Listing]:
    tree = HTMLParser(page_html)
    listings: list[Listing] = []
    for art in tree.css("article[data-adid]"):
        ad_id = art.attributes.get("data-adid") or ""
        href = art.attributes.get("data-href") or ""
        if not ad_id or not href:
            continue

        title_node = art.css_first("h3 a") or art.css_first("h3") or art.css_first("h2 a")
        title = _text(title_node)

        # Ort und Datum stehen als <span> in der Kopfzeile.
        location, posted = "", ""
        for span in art.css("span"):
            t = span.text(strip=True)
            if not posted and _DATE_RE.match(t):
                posted = t
            elif not location and _ZIP_RE.match(t):
                location = t

        snippet = ""
        if title_node is not None:
            h3 = title_node if title_node.tag == "h3" else title_node.parent
            sib = h3.next if h3 is not None else None
            while sib is not None and sib.tag != "p":
                sib = sib.next
            snippet = _text(sib)

        price, negotiable, old_price = None, False, None
        price_found = False
        for p in art.css("p"):
            t = p.text(strip=True)
            cls = p.attributes.get("class") or ""
            if "line-through" in cls:
                old_price, _ = parse_price(t)
                continue
            if price_found:
                continue
            if t and (t.endswith("€") or t.endswith("VB") or "€ VB" in t or t == "VB" or "verschenken" in t.lower()) and len(t) < 30:
                price, negotiable = parse_price(t)
                price_found = True

        badges = {s.text(strip=True) for s in art.css("span[data-dhl-promotion], span")}
        img = art.css_first("img")
        image_url = None
        if img is not None:
            image_url = img.attributes.get("src")
            if image_url:
                image_url = re.sub(r"rule=\$_\d+\.", "rule=$_59.", image_url)  # größere Variante

        listings.append(
            Listing(
                ad_id=ad_id,
                url=BASE_URL + href if href.startswith("/") else href,
                title=html_lib.unescape(title),
                price=price,
                negotiable=negotiable,
                location=location,
                posted=posted,
                snippet=html_lib.unescape(snippet),
                image_url=image_url,
                shipping="Versand möglich" in badges,
                is_pro_seller=art.css_first('a[href^="/pro/"]') is not None,
                is_wanted="Gesuch" in badges,
                old_price=old_price,
                direct_buy="Direkt kaufen" in badges,
                source_query=source_query,
            )
        )
    return listings


_DFP_RE = re.compile(r'"Modell":"[^"]*".*?"user_type":"[^"]*"', re.S)


def _extract_dfp_attributes(page_html: str) -> dict[str, str]:
    """Die Detailseite enthält Werbe-Targeting-JSON mit normalisierten Attributen
    (Modell=ps5_slim, Zustand=defect, Versand=nein, Verkaeufer=privat ...)."""
    attrs: dict[str, str] = {}
    idx = page_html.find('"ExactPreis"')
    if idx == -1:
        return attrs
    start = page_html.rfind("{", 0, idx)
    end = page_html.find("}", idx)
    if start == -1 or end == -1:
        return attrs
    try:
        data = json.loads(page_html[start : end + 1])
    except json.JSONDecodeError:
        for k, v in re.findall(r'"([A-Za-z_]+)":"([^"]*)"', page_html[start : end + 1]):
            attrs[k] = v
        return attrs
    return {k: str(v) for k, v in data.items() if isinstance(v, (str, int, float))}


def parse_detail_page(page_html: str, listing: Listing) -> Listing:
    """Ergänzt ein Listing um Beschreibung, Attribute, Bilder und Verkäuferinfos."""
    tree = HTMLParser(page_html)

    title = tree.css_first("#viewad-title")
    if title is None:
        listing.gone = True
        listing.detail_loaded = True
        return listing
    title_text = title.text(strip=True)
    if re.search(r"^(Reserviert|Gelöscht|Verkauft)\s*•", title_text):
        listing.gone = True
    if not listing.title:
        listing.title = html_lib.unescape(title_text)
    if not listing.location:
        listing.location = _text(tree.css_first("#viewad-locality"))

    desc = tree.css_first("#viewad-description-text")
    if desc is not None:
        listing.description = html_lib.unescape(desc.text(separator="\n", strip=True))

    price_node = tree.css_first("#viewad-price")
    if price_node is not None:
        price, neg = parse_price(price_node.text(strip=True))
        if price is not None:
            listing.price, listing.negotiable = price, neg

    shipping = tree.css_first(".boxedarticle--details--shipping")
    if shipping is not None and "Versand" in shipping.text() and "Nur Abholung" not in shipping.text():
        listing.shipping = True

    # Sichtbare Attributliste (Art, Modell, Zustand, Versand ...)
    for li in tree.css("li.addetailslist--detail"):
        value = li.css_first(".addetailslist--detail--value")
        if value is None:
            continue
        key = li.text(deep=False, strip=True) or li.text(strip=True).replace(value.text(strip=True), "").strip()
        listing.attributes[key] = value.text(strip=True)

    for k, v in _extract_dfp_attributes(page_html).items():
        if k in {"Modell", "Zustand", "Versand", "Verkaeufer", "Anzahl_Controller", "Speicher__GB_", "Farbe", "ExactPreis", "Angebotstyp"}:
            listing.attributes.setdefault(f"_{k}", v)

    # Bilder (in Reihenfolge der Galerie)
    seen: set[str] = set()
    for m in re.finditer(r'https://img\.kleinanzeigen\.de/api/v1/prod-ads/images/[0-9a-f]{2}/[0-9a-f-]{36}', page_html):
        base = m.group(0)
        if base not in seen:
            seen.add(base)
            listing.image_urls.append(base + "?rule=$_59.AUTO")
    # Die Galerie steht vor "Ähnliche Anzeigen" – deren Bilder sollen nicht mitkommen.
    gallery_end = page_html.find("viewad-description")
    if gallery_end > 0:
        gallery_ids = set(re.findall(r'images/[0-9a-f]{2}/([0-9a-f-]{36})', page_html[:gallery_end]))
        listing.image_urls = [u for u in listing.image_urls if u.split("/")[-1].split("?")[0] in gallery_ids]

    # Verkäufer
    seller_link = tree.css_first(".userprofile-vip a")
    if seller_link is not None:
        m = re.search(r"userId=(\d+)", seller_link.attributes.get("href") or "")
        if m:
            listing.seller_user_id = m.group(1)
    for detail in tree.css(".userprofile-vip-details-text"):
        t = detail.text(strip=True)
        if t.startswith("Aktiv seit"):
            listing.seller_since = t.replace("Aktiv seit", "").strip()
        elif "Privater Nutzer" in t:
            listing.seller_type = "privat"
        elif "Gewerblich" in t:
            listing.seller_type = "gewerblich"
    if listing.seller_type is None:
        v = listing.attributes.get("_Verkaeufer")
        if v:
            listing.seller_type = "privat" if v == "privat" else "gewerblich"
    listing.seller_badges = [
        b.text(strip=True).replace("\xa0", " ") for b in tree.css(".profile-userbadges .userbadge-tag") if b.text(strip=True)
    ]

    listing.detail_loaded = True
    return listing
