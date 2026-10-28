"""Regelbasierte Bewertung eines Inserats: Relevanz, Modell, Defekte, Wert, Gewinn, Score.

Läuft in Mikrosekunden und entscheidet, welche Inserate überhaupt Detailseite + KI bekommen.
"""

from __future__ import annotations

import re
from datetime import datetime

from .config import Config
from .models import Analysis, Defect, Listing, Model


def normalize(text: str) -> str:
    text = (text or "").lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"), (" ", " ")):
        text = text.replace(a, b)
    return re.sub(r"[ \t]+", " ", text)


def _any(patterns: list[str], text: str) -> str | None:
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(0)
    return None


# ---------------------------------------------------------------------------
# Relevanz
# ---------------------------------------------------------------------------

PS5_RE = r"\bps\s?5\b|playstation\W{0,3}5\b|\bps5"

NOT_A_SALE = [
    r"\bsuche\b", r"\bgesucht\b", r"\bankauf\b", r"\bkaufe\b", r"\bwir kaufen\b",
    r"\bsuche nach\b",
]
TRADE = r"\btausch(e|en)?\b"
SERVICE = [
    r"reparatur ?service", r"\bwir reparieren\b", r"\brepariere\b", r"reparatur ab\b",
    r"\breparatur\b.*\b(macbook|handy|iphone|laptop)", r"\bservice\b.*\breparatur",
]

# Konsolen-Hinweise im Titel – schlagen Zubehör-Wörter ("PS5 Slim mit Controller")
CONSOLE_HINTS = [
    r"\bkonsole\b", r"\bconsole\b", r"\bslim\b", r"\bps\s?5 ?pro\b", r"playstation 5 pro\b",
    r"disc[- ]?edition", r"disk[- ]?edition", r"digital[- ]?edition", r"\bdigital\b",
    r"\b825 ?gb\b", r"\b1 ?tb\b", r"\b2 ?tb\b", r"\bcfi[- ]?\d{4}", r"\b(mit|ohne) laufwerk\b",
    r"\bbundle\b", r"\bedition\b", r"\bmit (\d|zwei|2|einem|1) ?controller",
    r"\binkl\.?\b", r"\b\+ ?(\d|zwei|2) ?controller", r"\bovp\b",
]
ACCESSORY = [
    r"\bcontroller\b", r"\bdualsense\b", r"\bspiele?\b", r"\bgames?\b", r"\bspielesammlung\b",
    r"\bheadset\b", r"\bpulse\b", r"\bvr ?2?\b", r"\blaufwerk\b", r"\bnetzteil\b", r"\bluefter\b",
    r"\bcover\b", r"\bhuelle\b", r"\bfaceplate\b", r"\bskin\b", r"\bstaender\b", r"\bstandfuss\b",
    r"\bladestation\b", r"\bcharging\b", r"\bkabel\b", r"\bportal\b", r"\blenkrad\b", r"\bedge\b",
    r"\bfernbedienung\b", r"\bremote\b", r"\bkamera\b", r"\bcamera\b", r"\bssd\b", r"\bfestplatte\b",
    r"\bgehaeuse\b", r"\bmainboard\b", r"\bplatine\b", r"\bseitenteile?\b", r"\bcode\b",
    r"\bsteelbook\b", r"\bdisc only\b", r"\bfc ?\d{2}\b", r"\bfifa\b", r"\bcall of duty\b",
    r"\bspider-?man\b", r"\bgta\b", r"\bkuehler\b", r"\bhalterung\b", r"\bwandhalterung\b",
    r"\btasche\b", r"\bkoffer\b", r"\bmod\b", r"\bscuf\b", r"\bjoystick\b", r"\bsticks?\b",
]

# Ohne Konsolen-Hinweis im Titel immer ablehnen – egal wie teuer.
HARD_ACCESSORY = [
    r"\bcontroller\b", r"\bdualsense\b", r"\bspiel\b", r"\bspielesammlung\b", r"\bportal\b",
    r"\bvr ?2\b", r"\bheadset\b", r"\blenkrad\b", r"\bsteelbook\b", r"\bpulse\b", r"\bscuf\b",
]

# ---------------------------------------------------------------------------
# Defekte
# ---------------------------------------------------------------------------

D = r"(defekt|kaputt|abgebrochen|locker|wackel\w*|beschaedigt|ausgerissen|verbogen|reparatur\w*|tauschen|geht nicht|funktioniert nicht)"

DEFECT_PATTERNS: dict[str, list[str]] = {
    "hdmi": [
        rf"hdmi[\w\s\-/]{{0,25}}{D}", rf"{D}[\w\s\-/]{{0,15}}hdmi",
        r"kein (bild|signal|hdmi[- ]?signal|display)", r"keine bildausgabe", r"bildausgabe[\w\s]{0,15}(defekt|geht nicht)",
        r"(bildschirm|tv|fernseher) bleibt schwarz", r"black ?screen", r"no signal",
    ],
    "psu": [
        rf"netzteil[\w\s\-]{{0,25}}{D}", r"(geht|startet|springt) (gar )?(nicht|nich) (mehr )?an",
        r"laesst sich nicht (mehr )?(ein|an)schalten", r"schaltet (sich )?nicht (mehr )?ein",
        r"keine? (strom|reaktion|lebenszeichen)", r"\bohne funktion\b", r"\btot\b",
        r"kein (piepen|piep|ton beim einschalten|licht)", r"\bohne netzteil\b", r"netzteil fehlt",
    ],
    "thermal": [
        r"ueberhitz\w*", r"wird (sehr |zu |extrem )?heiss", r"zu heiss",
        r"schaltet (sich )?(selbst(staendig)?|einfach|ploetzlich|immer|nach \w+ \w+|waehrend \w+)? ?(aus|ab)\b",
        r"geht (einfach|ploetzlich|immer|von selbst|waehrend|nach)[\w\s]{0,25} aus\b",
        r"stuerzt (staendig |immer |oft )?ab", r"\babstuerze?\b", r"waermeleitpaste", r"fluessigmetall",
    ],
    "fan": [
        r"luefter[\w\s\-]{0,20}(laut|defekt|kaputt|dreht|schleift|geraeusch|klackert|rattert)",
        r"(sehr|extrem|ungewoehnlich) laut", r"laute? geraeusche?", r"\brattert\b", r"\bschleift\b",
    ],
    "drive": [
        rf"laufwerk[\w\s\-]{{0,25}}({D}|liest|erkennt|zieht|spuckt)",
        r"(disc|disk|discs|cds?|spiele|blu-?ray)[\w\s\-]{0,25}nicht (mehr )?(erkannt|gelesen|eingezogen|abgespielt)",
        r"liest keine", r"zieht (keine|die) (disc|cd|spiele)",
    ],
    "software": [
        r"abgesicherte[nr]? modus", r"safe ?mode", r"software ?(fehler|problem)", r"systemsoftware",
        r"firmware[\w\s]{0,15}(fehler|defekt|kaputt|problem)", r"\bce-\d{5,6}", r"fehlercode",
        r"update[\w\s]{0,15}(fehlgeschlagen|abgebrochen|haengt)", r"boot ?loop", r"haengt (im|beim) (start|logo|booten)",
    ],
    "usb": [rf"usb[\w\s\-]{{0,20}}{D}"],
    "unknown_defect": [
        r"blau\w* (licht|leuchten|blinken|pulsier\w*)", r"blue ?light", r"\bblod\b",
        r"(weiss|orange)\w*[\w\s]{0,10}blink\w*",
    ],
}

GENERIC_DEFECT = [
    r"\bdefekt\w*", r"\bkaputt\b", r"\bbastler\w*", r"\bersatzteil\w*", r"\bfuer teile\b",
    r"funktioniert (nicht|nich)", r"\bgeht nicht\b", r"\bdef\.", r"\bals defekt\b", r"\bfehlerhaft\b",
    r"\bbroken\b", r"\bspender\b",
]
NEGATION = r"(nicht|kein|keine|keinerlei|ohne|nie)\s+(\w+\s+)?$"

DEAL_BREAKERS = {
    "Wasser-/Flüssigkeitsschaden": r"wasserschaden|fluessigkeit\w*schaden|fluessigkeit (eingedrungen|drin)|wasser (eingedrungen|drueber|reingekommen)|getraenk\w* (drueber|ausgekippt)",
    "Mainboard/APU-Schaden": r"\bapu\b|\bcpu\b[\w\s]{0,20}(defekt|kaputt|tauschen|ausgetauscht|getauscht)|mainboard[\w\s]{0,20}(defekt|kaputt|beschaedigt)|platine[\w\s]{0,20}(defekt|kaputt)|board ?(defekt|schaden)|kurzschluss",
    "Brand-/Hitzeschaden": r"brandschaden|verbrannt|geschmort|durchgeschmort|\\braucht\\b|rauchentwicklung",
    "Konsole gesperrt/gebannt": r"gesperrt|gebannt|\bbann?ed\b|konsolensperre|psn[\w\s]{0,10}sperre",
    "Unvollständig (Kernteile fehlen)": r"ohne (mainboard|platine|apu|ssd|speicher)\b|nur (das )?gehaeuse|leergehaeuse|leeres gehaeuse|ausgeschlachtet",
    "SSD/Speicher defekt": r"(ssd|speicher|nand)[\w\s]{0,15}(defekt|kaputt|fehler)",
}

PREVIOUS_REPAIR = r"(bereits|schon) (geoeffnet|repariert|versucht)|reparaturversuch|rumgeloetet|selbst (geoeffnet|repariert)|von (einem )?(fachmann|werkstatt) (geprueft|angeschaut|geoeffnet)"

# ---------------------------------------------------------------------------
# Betrugsindikatoren & Pluspunkte
# ---------------------------------------------------------------------------

SCAM_TEXT = {
    "Kontakt außerhalb (WhatsApp/Mail)": r"whats ?app|\bwa\b|e-?mail an|schreib(t)? mir (auf|per)|telegram|\+49 ?\d{3}|\b01[5-7]\d[ /-]?\d{6,}",
    "Nur Vorkasse/PayPal Freunde": r"paypal (freunde|f&f|friends)|\bvorkasse\b|nur ueberweisung",
    "Auslands-Story": r"(im|ins) ausland|bin (beruflich|derzeit) (in|im)|umzug nach",
    "Englischer Text": r"\b(hello|shipping|i am selling|brand new|condition)\b",
}

BONUSES = {
    "OVP": (r"\bovp\b|originalverpackung|original ?karton|\bkarton\b", 15),
    "2+ Controller": (r"\b(2|zwei|3|drei)\s?(x\s)?(original(e|en)?\s)?(dualsense\s)?controller|\+ ?(1|ein) weitere[nr]? controller", 35),
    "Spiele dabei": (r"\b(mit|inkl\.?|\+|plus)\s?(\d+\s)?(spiele|games)\b|\bspiele (dabei|inklusive)", 15),
    "Rechnung/Garantie": (r"\brechnung\b|\bgarantie\b|\bkassenbon\b", 15),
}

CONTROLLER_MISSING = r"ohne controller|kein controller|controller fehlt|nur (die )?konsole"


def detect_model(listing: Listing) -> Model:
    attr = (listing.attributes.get("_Modell") or listing.attributes.get("Modell") or "").lower()
    text = normalize(listing.full_text)
    title = normalize(listing.title)

    cfi = re.search(r"cfi[- ]?(\d{2})\d{2}\s?([ab])?", text)
    if cfi:
        series, variant = cfi.group(1), (cfi.group(2) or "")
        if series.startswith("7"):
            return Model.PRO
        if series.startswith("2"):
            return Model.SLIM_DIGITAL if variant == "b" or "digital" in text else Model.SLIM_DISC
        if series.startswith("1"):
            return Model.DIGITAL if variant == "b" else Model.DISC

    is_pro = "pro" in attr or re.search(r"(ps\s?5|playstation\s?5)\s*pro\b", title) or re.search(r"(ps\s?5|playstation\s?5)\s*pro\b", text)
    if is_pro:
        return Model.PRO

    digital = re.search(r"digital|ohne (disc-?|disk-?)?laufwerk|ohne disc", text) and not re.search(r"(mit|inkl\.?|\+) ?(disc-?|disk-?)?laufwerk|disc[- ]?edition|disk[- ]?edition", text)
    disc = re.search(r"disc|disk|laufwerk|blu-?ray|standard[- ]?edition", text)
    slim = "slim" in attr or re.search(r"\bslim\b", text)

    if slim:
        if digital:
            return Model.SLIM_DIGITAL
        return Model.SLIM_DISC if disc else Model.SLIM_DIGITAL  # unklar -> konservativ
    if "digital" in attr or digital:
        return Model.DIGITAL
    if disc or attr in {"ps5", "ps5_disc"}:
        return Model.DISC
    return Model.UNKNOWN


def _negated(text: str, start: int) -> bool:
    return re.search(NEGATION, text[max(0, start - 25) : start]) is not None


def detect_defects(listing: Listing, cfg: Config) -> tuple[bool, list[Defect]]:
    text = normalize(listing.full_text)
    zustand = (listing.attributes.get("_Zustand") or "").lower()
    defects: list[Defect] = []
    for key, patterns in DEFECT_PATTERNS.items():
        for p in patterns:
            m = re.search(p, text)
            if m and not _negated(text, m.start()):
                cost, difficulty, skill = cfg.repair.get(key, cfg.repair["unknown_defect"])
                label = {
                    "hdmi": "HDMI / kein Bild", "psu": "Netzteil / startet nicht",
                    "thermal": "Überhitzung / schaltet ab", "fan": "Lüfter / laut",
                    "drive": "Laufwerk", "software": "Software / abgesicherter Modus",
                    "usb": "USB-Port", "unknown_defect": "Unklarer Fehler (Licht-Blinken)",
                }[key]
                defects.append(Defect(key, f"{label} („{m.group(0).strip()}“)", int(cost), difficulty, bool(skill)))
                break

    generic = False
    for p in GENERIC_DEFECT:
        for m in re.finditer(p, text):
            if not _negated(text, m.start()):
                generic = True
                break
        if generic:
            break

    defective = bool(defects) or generic or zustand == "defect"
    if defective and not defects:
        cost, difficulty, skill = cfg.repair["unknown_defect"]
        defects.append(Defect("unknown_defect", "Defekt, Ursache nicht genannt", int(cost), difficulty, bool(skill)))
    return defective, defects


def posted_minutes_ago(posted: str, now: datetime) -> float | None:
    """'Heute, 22:40' -> Minuten seit Einstellung. None = unbekannt (Topanzeige)."""
    m = re.match(r"(Heute|Gestern),\s*(\d{1,2}):(\d{2})", posted or "")
    if not m:
        if re.match(r"\d{2}\.\d{2}\.\d{4}", posted or ""):
            return 24 * 60 * 2
        return None
    t = now.replace(hour=int(m.group(2)), minute=int(m.group(3)), second=0, microsecond=0)
    minutes = (now - t).total_seconds() / 60
    if m.group(1) == "Gestern":
        minutes += 24 * 60
    return max(minutes, 0.0) if minutes > -5 else minutes + 24 * 60


def check_relevance(listing: Listing, cfg: Config) -> str | None:
    """Gibt einen Ablehnungsgrund zurück oder None, wenn das Inserat eine PS5-Konsole sein kann."""
    title = normalize(listing.title)
    text = normalize(listing.full_text)
    if listing.is_wanted:
        return "Gesuch"
    if _any(NOT_A_SALE, title):
        return "Suche/Ankauf"
    if re.search(TRADE, title) and not re.search(r"verkauf|oder tausch|vb", title):
        return "Tausch"
    if _any(SERVICE, title) or _any(SERVICE, text[:300]):
        return "Reparatur-Dienstleistung"
    if listing.is_pro_seller and cfg.exclude_pro_sellers:
        return "Gewerblicher PRO-Shop"
    if not re.search(PS5_RE, title):
        if not re.search(PS5_RE, text):
            return "Keine PS5 erwähnt"
        if not re.search(r"\bkonsole\b|playstation|\bps\b", title):
            return "PS5 nur in Beschreibung"

    has_console = _any(CONSOLE_HINTS, title)
    has_accessory = _any(ACCESSORY, title)
    hard_accessory = _any(HARD_ACCESSORY, title)
    if hard_accessory and not has_console:
        return f"Zubehör/Spiel ({hard_accessory})"
    if has_accessory and not has_console and (listing.price is None or listing.price < 150):
        # Teure Inserate wie "PS5 Laufwerk defekt – 180 €" sind oft doch Konsolen -> KI entscheidet.
        return f"Zubehör/Spiel ({has_accessory})"
    if listing.price is not None and listing.price < cfg.min_price:
        return f"Preis unter {cfg.min_price} €"
    if listing.price is not None and listing.price > cfg.max_price:
        return f"Preis über {cfg.max_price} €"
    if not has_console and listing.price is not None and listing.price < 120:
        defect_word = _any(GENERIC_DEFECT, text)
        if not defect_word:
            return "Vermutlich Spiel/Zubehör (billig, kein Konsolenhinweis)"
    return None


def analyze(listing: Listing, cfg: Config, resale_table: dict[str, int], now: datetime | None = None) -> Analysis:
    now = now or datetime.now()
    reason = check_relevance(listing, cfg)
    model = detect_model(listing)
    if reason:
        return Analysis(False, reason, model, False, [], [], [], [], None, 0, None, 0.0)

    text = normalize(listing.full_text)
    defective, defects = detect_defects(listing, cfg)

    deal_breakers = [label for label, p in DEAL_BREAKERS.items() if (m := re.search(p, text)) and not _negated(text, m.start())]

    red_flags: list[str] = []
    notes: list[str] = []
    for label, p in SCAM_TEXT.items():
        if re.search(p, text):
            red_flags.append(label)
    if re.search(PREVIOUS_REPAIR, text):
        red_flags.append("Bereits geöffnet / Reparaturversuch")
    if listing.seller_since:
        try:
            since = datetime.strptime(listing.seller_since, "%d.%m.%Y")
            if (now - since).days < 14:
                red_flags.append(f"Neues Konto (seit {listing.seller_since})")
        except ValueError:
            pass
    if listing.seller_type == "gewerblich":
        notes.append("Gewerblicher Verkäufer")

    bonuses: list[str] = []
    bonus_value = 0
    for label, (p, value) in BONUSES.items():
        if re.search(p, text):
            bonuses.append(label)
            bonus_value += value
    if listing.negotiable:
        bonuses.append("VB")
    if listing.old_price and listing.price is not None and listing.old_price > listing.price:
        bonuses.append(f"Preis gesenkt ({listing.old_price} → {listing.price} €)")

    base = resale_table.get(model.value, resale_table.get("unknown", 380))
    resale_value = base + bonus_value

    repair_cost = 0
    if re.search(CONTROLLER_MISSING, text):
        cost = int(cfg.repair["controller_missing"][0])
        repair_cost += cost
        notes.append(f"Kein Controller (+{cost} €)")
    # Mehrere Symptome derselben Ursache nicht doppelt zählen: teuerste + 50% der restlichen.
    costs = sorted((d.repair_cost for d in defects), reverse=True)
    if costs:
        repair_cost += costs[0] + sum(costs[1:]) // 2

    expected_profit = None
    if listing.price is not None:
        expected_profit = resale_value - listing.price - repair_cost - cfg.selling_overhead

    # ---- Score ----
    score = 0.0
    if expected_profit is None:
        score = 45.0 if defective else 25.0
        notes.append("Kein Preis angegeben – Preisvorschlag nötig")
    else:
        invested = max(listing.price + repair_cost, 50)
        score += max(min(expected_profit / 150 * 60, 60), -40)
        score += max(min(expected_profit / invested * 40, 25), 0)
    if defective:
        if defects and all(d.in_skillset for d in defects):
            score += 10
        elif any(not d.in_skillset for d in defects):
            score -= 8
    if listing.seller_type == "privat":
        score += 4
    if listing.negotiable:
        score += 3
    if listing.shipping:
        score += 2
    if model is Model.UNKNOWN:
        score -= 5
    title = normalize(listing.title)
    console_confirmed = bool(_any(CONSOLE_HINTS, title)) or bool(listing.attributes.get("_Modell"))
    if _any(ACCESSORY, title) and not _any(CONSOLE_HINTS, title):
        score -= 20
        notes.append("Titel klingt nach Zubehör – KI prüft")
    score -= 12 * len(red_flags)
    if deal_breakers:
        score -= 45

    # Zu billig für eine funktionierende Konsole -> Betrugsverdacht
    if not defective and listing.price is not None and listing.price < 0.45 * base:
        red_flags.append("Unrealistisch günstig für funktionierende Konsole")
        score -= 25

    return Analysis(
        relevant=True,
        reject_reason=None,
        model=model,
        defective=defective,
        defects=defects,
        deal_breakers=deal_breakers,
        red_flags=red_flags,
        bonuses=bonuses,
        resale_value=resale_value,
        repair_cost=repair_cost,
        expected_profit=expected_profit,
        score=max(0.0, min(100.0, score)),
        notes=notes,
        console_confirmed=console_confirmed,
    )
