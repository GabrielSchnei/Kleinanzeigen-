"""Datenklassen, die zwischen Scraper, Analyse, KI und Telegram ausgetauscht werden."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Model(str, Enum):
    """PS5-Modellvarianten. Der Wert wird in Config und Datenbank verwendet."""

    DISC = "disc"            # PS5 Original (Fat) mit Laufwerk
    DIGITAL = "digital"      # PS5 Original Digital Edition
    SLIM_DISC = "slim_disc"  # PS5 Slim mit Laufwerk
    SLIM_DIGITAL = "slim_digital"
    PRO = "pro"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return {
            Model.DISC: "PS5 Disc (Fat)",
            Model.DIGITAL: "PS5 Digital (Fat)",
            Model.SLIM_DISC: "PS5 Slim Disc",
            Model.SLIM_DIGITAL: "PS5 Slim Digital",
            Model.PRO: "PS5 Pro",
            Model.UNKNOWN: "PS5 (Modell unklar)",
        }[self]


@dataclass
class Listing:
    """Ein Inserat. Felder aus der Trefferliste sind immer gesetzt, Detailfelder erst nach `enrich`."""

    ad_id: str
    url: str
    title: str
    price: int | None             # in Euro, None = kein Preis / "Zu verschenken" / nur VB
    negotiable: bool               # "VB"
    location: str
    posted: str                    # z.B. "Heute, 22:40"
    snippet: str                   # gekürzte Beschreibung aus der Trefferliste
    image_url: str | None = None
    shipping: bool = False
    is_pro_seller: bool = False    # gewerblicher "PRO"-Shop
    is_wanted: bool = False        # "Gesuch"
    old_price: int | None = None   # durchgestrichener Preis (Preissenkung)
    direct_buy: bool = False       # "Direkt kaufen"
    source_query: str = ""

    # --- Detailseite ---
    description: str | None = None
    attributes: dict[str, str] = field(default_factory=dict)  # z.B. Modell, Zustand, Versand
    image_urls: list[str] = field(default_factory=list)
    seller_type: str | None = None       # "privat" / "gewerblich"
    seller_since: str | None = None      # "28.07.2021"
    seller_badges: list[str] = field(default_factory=list)
    seller_user_id: str | None = None
    detail_loaded: bool = False
    gone: bool = False                   # gelöscht/verkauft/reserviert

    @property
    def full_text(self) -> str:
        return f"{self.title}\n{self.description or self.snippet}"


@dataclass
class Defect:
    key: str
    label: str
    repair_cost: int
    difficulty: str        # "leicht" / "mittel" / "schwer" / "nein"
    in_skillset: bool      # vom Nutzer selbst reparierbar


@dataclass
class Analysis:
    """Ergebnis der regelbasierten Bewertung."""

    relevant: bool                     # ist es überhaupt eine PS5-Konsole zum Verkauf?
    reject_reason: str | None
    model: Model
    defective: bool
    defects: list[Defect]
    deal_breakers: list[str]           # z.B. Wasserschaden, APU, gebannt
    red_flags: list[str]               # Betrugsindikatoren
    bonuses: list[str]                 # z.B. OVP, 2 Controller, Spiele
    resale_value: int | None           # erwarteter Verkaufspreis nach Reparatur
    repair_cost: int
    expected_profit: int | None
    score: float                       # 0..100
    notes: list[str] = field(default_factory=list)
    console_confirmed: bool = False    # Titel/Attribute belegen eindeutig eine Konsole


@dataclass
class AIVerdict:
    is_deal: bool
    confidence: float                  # 0..1
    model_guess: str
    condition_summary: str
    likely_fault: str
    repairable_by_user: bool
    estimated_resale: int | None
    estimated_repair_cost: int | None
    max_buy_price: int | None
    suggested_offer: int | None
    scam_risk: str                     # "niedrig" / "mittel" / "hoch"
    reasoning: str
    message_draft: str
