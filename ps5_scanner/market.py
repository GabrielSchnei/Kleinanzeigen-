"""Dynamische Marktpreise: lernt aus funktionsfähigen Inseraten, was eine PS5 gerade kostet.

Angebotspreise liegen über den tatsächlichen Verkaufspreisen, daher wird der Median mit einem
Abschlag versehen und mit den Konfigurationswerten gemischt (je mehr Daten, desto mehr Markt).
"""

from __future__ import annotations

from .config import Config
from .models import Analysis, Listing, Model
from .storage import Storage

ASKING_TO_SALE = 0.90     # Angebotspreis -> realistischer Verkaufspreis
MIN_SAMPLES = 8
MAX_MARKET_WEIGHT = 0.8


class Market:
    def __init__(self, cfg: Config, storage: Storage):
        self.cfg = cfg
        self.storage = storage
        self._cache: dict[str, int] | None = None

    def observe(self, listing: Listing, analysis: Analysis) -> None:
        """Speichert Preise funktionierender Konsolen von Privatverkäufern als Marktdaten."""
        if (
            not self.cfg.dynamic_market
            or not analysis.relevant
            or analysis.defective
            or analysis.model is Model.UNKNOWN
            or listing.price is None
            or listing.is_pro_seller
            or analysis.red_flags
            or not analysis.console_confirmed
        ):
            return
        base = self.cfg.resale.get(analysis.model.value)
        if not base or not (0.6 * base <= listing.price <= 1.8 * base):
            return  # Ausreißer (Bundles, Fantasiepreise, Betrug)
        self.storage.add_market_price(listing.ad_id, analysis.model.value, listing.price)
        self._cache = None

    def resale_table(self) -> dict[str, int]:
        if self._cache is not None:
            return self._cache
        table = dict(self.cfg.resale)
        if self.cfg.dynamic_market:
            for model in Model:
                if model is Model.UNKNOWN:
                    continue
                median, n = self.storage.market_median(model.value)
                if median is None or n < MIN_SAMPLES:
                    continue
                weight = min(n / 40, MAX_MARKET_WEIGHT)
                table[model.value] = int(round((1 - weight) * table[model.value] + weight * median * ASKING_TO_SALE))
            known = [table[m.value] for m in (Model.DISC, Model.DIGITAL, Model.SLIM_DISC, Model.SLIM_DIGITAL)]
            table["unknown"] = min(known)
        self._cache = table
        return table

    def describe(self) -> str:
        table = self.resale_table()
        lines = []
        for model in Model:
            if model is Model.UNKNOWN:
                continue
            median, n = self.storage.market_median(model.value)
            market = f"Markt-Median {int(median)} € ({n} Inserate)" if median else "noch keine Marktdaten"
            lines.append(f"• {model.label}: <b>{table[model.value]} €</b> – {market}")
        return "\n".join(lines)
