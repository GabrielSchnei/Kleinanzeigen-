"""KI-Zweitmeinung mit Claude: liest Beschreibung + Bilder wie ein erfahrener Ankäufer."""

from __future__ import annotations

import json
import logging

import anthropic

from .config import Config
from .models import AIVerdict, Analysis, Listing

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Du bist ein erfahrener Ankäufer für gebrauchte und defekte PlayStation-5-Konsolen in Deutschland.
Dein Auftraggeber kauft PS5-Konsolen auf Kleinanzeigen günstig, repariert sie bei Bedarf und verkauft sie mit Gewinn weiter.

Was der Auftraggeber selbst reparieren kann:
- Netzteil tauschen (Konsole startet nicht / kein Strom)
- HDMI-Buchse neu löten (kein Bild, HDMI abgebrochen/wackelig)
- Wärmeleitpaste/Flüssigmetall erneuern, Reinigung, Lüfter tauschen (Überhitzung, schaltet ab, laut)
- Software neu aufspielen (abgesicherter Modus, Update-Fehler)
Nicht selbst reparierbar bzw. riskant: APU-/Mainboard-Schäden, Wasserschäden, SSD/NAND-Defekte, gebannte Konsolen,
Laufwerke (sind ans Mainboard gekoppelt – nur Laser/Mechanik tauschbar), Konsolen mit unbekannter Vorgeschichte "nach Reparaturversuch".

Deine Aufgabe: Beurteile ein einzelnes Inserat nüchtern und kaufmännisch.
- Erkenne das genaue Modell (Fat Disc/Digital, Slim Disc/Digital, Pro) aus Text, Attributen und Bildern.
- Erkenne den wahrscheinlichsten Fehler aus der Symptombeschreibung (z. B. "geht an, aber kein Bild" = meist HDMI-Buchse
  oder HDMI-Retimer-Chip; "piept und geht sofort aus" = oft Netzteil oder Kurzschluss; "schaltet beim Spielen ab" = Thermik).
- Prüfe Bilder auf sichtbare Schäden, abgebrochene Buchsen, Brandspuren, fehlende Teile, Stockfotos (Betrugsindiz).
- Schätze den realistischen Wiederverkaufspreis (funktionsfähig, nach Reparatur) anhand der gegebenen Marktwerte.
- Prüfe Betrugsrisiko: unrealistisch günstig, nur Versand + Vorkasse, Kontakt außerhalb Kleinanzeigen, Stockfotos,
  neues Konto, kopierte Texte.
- "Deal" bedeutet: Nach Kaufpreis, Teilekosten und ~15 € Nebenkosten bleiben realistisch mindestens {min_profit} € Gewinn,
  und das Risiko ist vertretbar. Ohne Preisangabe: Deal, wenn ein realistischer Einkaufspreis mit diesem Gewinn
  wahrscheinlich verhandelbar ist.
- max_buy_price: höchster Einkaufspreis, bei dem es sich noch lohnt. suggested_offer: sinnvolles erstes Angebot
  (bei VB etwas unter dem Preis, aber nicht unverschämt; bei Festpreis-Schnäppchen = Preis).
- message_draft: kurze, freundliche, natürliche Nachricht an den Verkäufer auf Deutsch (2–4 Sätze, per Du oder neutral,
  kein Gendern, keine Emojis, kein Hinweis auf Weiterverkauf). Frage, ob die Konsole noch da ist; bei Defekt nach 1 konkreten
  Detail zum Fehler fragen; nenne bei VB den Angebotspreis; schlage zeitnahe Abholung oder Versand vor.
  Keine Grußformel mit Namen – die Signatur wird automatisch angehängt.
Antworte ausschließlich im vorgegebenen JSON-Format. Wenn Werte unbekannt sind, nutze 0."""

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "is_deal": {"type": "boolean"},
        "confidence": {"type": "number", "description": "0 bis 1"},
        "model_guess": {"type": "string"},
        "condition_summary": {"type": "string", "description": "1 Satz Zustand/Lieferumfang"},
        "likely_fault": {"type": "string", "description": "wahrscheinlicher Fehler oder 'keiner'"},
        "repairable_by_user": {"type": "boolean"},
        "estimated_resale": {"type": "integer"},
        "estimated_repair_cost": {"type": "integer"},
        "max_buy_price": {"type": "integer"},
        "suggested_offer": {"type": "integer"},
        "scam_risk": {"type": "string", "enum": ["niedrig", "mittel", "hoch"]},
        "reasoning": {"type": "string", "description": "max. 2 kurze Sätze"},
        "message_draft": {"type": "string"},
    },
    "required": [
        "is_deal", "confidence", "model_guess", "condition_summary", "likely_fault", "repairable_by_user",
        "estimated_resale", "estimated_repair_cost", "max_buy_price", "suggested_offer", "scam_risk",
        "reasoning", "message_draft",
    ],
    "additionalProperties": False,
}


def _describe(listing: Listing, analysis: Analysis, resale: dict[str, int]) -> str:
    attrs = {k: v for k, v in listing.attributes.items() if not k.startswith("_")}
    price = f"{listing.price} €" if listing.price is not None else "kein Preis"
    if listing.negotiable:
        price += " VB"
    lines = [
        f"Titel: {listing.title}",
        f"Preis: {price}" + (f" (vorher {listing.old_price} €)" if listing.old_price else ""),
        f"Ort: {listing.location} | Versand möglich: {'ja' if listing.shipping else 'nein'}",
        f"Eingestellt: {listing.posted or 'unbekannt'}",
        f"Verkäufer: {listing.seller_type or 'unbekannt'}, aktiv seit {listing.seller_since or '?'}, "
        f"Bewertungen: {', '.join(listing.seller_badges) or 'keine'}",
        f"Attribute: {json.dumps(attrs, ensure_ascii=False)}",
        "",
        "Beschreibung:",
        (listing.description or listing.snippet or "(keine)")[:4000],
        "",
        "Vorbewertung durch Regeln:",
        f"- Modell erkannt: {analysis.model.label}",
        f"- Defekte erkannt: {', '.join(d.label for d in analysis.defects) or 'keine'}",
        f"- Ausschlussgründe: {', '.join(analysis.deal_breakers) or 'keine'}",
        f"- Warnsignale: {', '.join(analysis.red_flags) or 'keine'}",
        f"- Geschätzter Gewinn: {analysis.expected_profit if analysis.expected_profit is not None else 'unbekannt'} €",
        "",
        "Aktuelle Wiederverkaufswerte (funktionsfähig, gebraucht, 1 Controller): "
        + ", ".join(f"{k}={v} €" for k, v in resale.items()),
    ]
    return "\n".join(lines)


class AIFilter:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client = anthropic.AsyncAnthropic(
            api_key=cfg.anthropic_api_key or None,
            timeout=cfg.ai_timeout_seconds,
            max_retries=2,
        )
        self.system = SYSTEM_PROMPT.replace("{min_profit}", str(cfg.min_profit))

    async def evaluate(self, listing: Listing, analysis: Analysis, resale: dict[str, int],
                       use_images: bool | None = None) -> AIVerdict | None:
        if use_images is None:
            use_images = self.cfg.ai_use_images
        content: list[dict] = []
        if use_images:
            images = listing.image_urls or ([listing.image_url] if listing.image_url else [])
            for url in images[: self.cfg.ai_max_images]:
                content.append({"type": "image", "source": {"type": "url", "url": url}})
        content.append({"type": "text", "text": _describe(listing, analysis, resale)})

        try:
            response = await self.client.beta.messages.create(
                model=self.cfg.ai_model,
                max_tokens=4000,
                system=self.system,
                messages=[{"role": "user", "content": content}],
                output_config={
                    "effort": self.cfg.ai_effort,
                    "format": {"type": "json_schema", "schema": VERDICT_SCHEMA},
                },
                # Lehnt das Modell ab, springt serverseitig automatisch ein passendes Ersatzmodell ein.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.BadRequestError as e:
            if use_images:
                # z.B. Bild-URL nicht abrufbar -> ohne Bilder erneut versuchen
                log.warning("KI-Anfrage mit Bildern abgelehnt (%s) – versuche ohne Bilder", e.message)
                return await self.evaluate(listing, analysis, resale, use_images=False)
            log.error("KI-Anfrage ungültig: %s", e.message)
            return None
        except anthropic.RateLimitError:
            log.warning("KI Rate-Limit erreicht – Inserat ohne KI-Urteil")
            return None
        except anthropic.APIStatusError as e:
            log.error("KI-Fehler %s: %s", e.status_code, e.message)
            return None
        except anthropic.APIConnectionError as e:
            log.error("KI nicht erreichbar: %s", e)
            return None

        return self._parse(response)

    @staticmethod
    def _parse(response) -> AIVerdict | None:
        if response.stop_reason == "refusal":
            log.warning("KI hat die Bewertung abgelehnt")
            return None
        text = "".join(b.text for b in response.content if b.type == "text")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            log.error("KI-Antwort kein gültiges JSON (stop_reason=%s)", response.stop_reason)
            return None

        def opt(v):
            return int(v) if v else None

        return AIVerdict(
            is_deal=bool(data["is_deal"]),
            confidence=float(data["confidence"]),
            model_guess=data["model_guess"],
            condition_summary=data["condition_summary"],
            likely_fault=data["likely_fault"],
            repairable_by_user=bool(data["repairable_by_user"]),
            estimated_resale=opt(data["estimated_resale"]),
            estimated_repair_cost=int(data["estimated_repair_cost"] or 0),
            max_buy_price=opt(data["max_buy_price"]),
            suggested_offer=opt(data["suggested_offer"]),
            scam_risk=data["scam_risk"],
            reasoning=data["reasoning"],
            message_draft=data["message_draft"].strip(),
        )

