# 🎮 PS5-Deal-Scanner für Kleinanzeigen

Durchsucht Kleinanzeigen alle ~20 Sekunden nach neuen PlayStation-5-Angeboten – auch **defekten** – und schickt dir
nur die Inserate per **Telegram**, bei denen sich Kaufen (und ggf. Reparieren) und Weiterverkaufen lohnt.
Per Button kannst du den **Verkäufer direkt anschreiben**.

```
Kleinanzeigen (4 Suchen, neueste zuerst)
      │  alle ~20 s
      ▼
① Regel-Filter (Millisekunden)        Zubehör, Spiele, Gesuche, Shops, Tausch raus
      │                                Modell (Fat/Slim/Pro, Disc/Digital, CFI-Nummer)
      │                                Defekt-Erkennung: HDMI, Netzteil, Überhitzung, Lüfter, Software …
      │                                No-Gos: Wasserschaden, APU/Mainboard, gebannt …
      │                                Betrugssignale, Gewinn = Wert − Preis − Teile − Nebenkosten
      ▼
② Detailseite laden                   ganze Beschreibung, Attribute, Verkäufer, alle Bilder
      ▼
③ Claude-KI (Text + Bilder)           wahrscheinlicher Fehler, machbar für dich?, Betrugsrisiko,
      │                                Max-Einkaufspreis, Angebotsvorschlag, Nachrichtenentwurf
      ▼
④ Telegram (06:00–23:00)              Foto + Analyse + Buttons [✉️ Anschreiben] [📝 Text] [🔗] [🙈]
```

**Geschwindigkeit:** Sehr starke Deals (Score ≥ 80, klare Konsole, kein Warnsignal) werden **sofort** gesendet –
die KI-Bewertung wird ein paar Sekunden später in dieselbe Nachricht eingefügt. Grenzfälle gehen erst durch die KI.

**Nachts (23–6 Uhr)** wird weiter gescannt, aber nichts gesendet. Um 6 Uhr kommt ein kurzer Morgen-Überblick mit den
besten Nacht-Deals, **die noch online sind**.

**Marktpreise lernen:** Der Bot merkt sich die Preise funktionierender Konsolen und passt die Wiederverkaufswerte
automatisch an den aktuellen Markt an (`/preise` in Telegram zeigt sie).

---

## 1. Einrichtung (ca. 10 Minuten)

### Telegram-Bot anlegen
1. In Telegram **@BotFather** öffnen → `/newbot` → Namen vergeben → du bekommst einen **Token**.
2. Deinem neuen Bot einmal `/start` schreiben (sonst darf er dir nicht schreiben).
3. Deine **Chat-ID** herausfinden: **@userinfobot** anschreiben, er antwortet mit deiner ID.

### Claude-API-Key (für die KI-Prüfung)
Auf <https://console.anthropic.com> einen API-Key erstellen und etwas Guthaben aufladen.
Ohne Key läuft der Scanner trotzdem – dann nur mit dem Regel-Filter.

### Dateien anlegen
```bash
cp .env.example .env               # Token, Chat-ID, API-Key eintragen
cp config.example.yaml config.yaml # optional anpassen
```

---

## 2. Starten

### Variante A – auf deinem Windows-PC (am einfachsten, Anschreiben funktioniert direkt)
1. Python 3.11+ von <https://python.org> installieren (Haken bei „Add to PATH“).
2. Doppelklick auf **`start_windows.bat`** – beim ersten Mal wird alles installiert und die `.env` geöffnet.
3. Ausfüllen, speichern, `start_windows.bat` erneut starten. Fertig – in Telegram kommt „🚀 Scanner gestartet“.

Der PC muss dafür laufen. Tipp: Energiesparmodus aus, Skript in den Autostart legen.

### Variante B – 24/7 auf einem kleinen Server (empfohlen für Dauerbetrieb)
Ein günstiger VPS (z. B. Hetzner/Netcup, ~4 €/Monat) mit Docker:
```bash
git clone <dieses-repo> && cd <repo>
cp .env.example .env && nano .env
cp config.example.yaml config.yaml
docker compose up -d --build
docker compose logs -f
```
Für das Anschreiben auf dem Server: in `docker-compose.yml` `WITH_BROWSER: "1"` setzen und den Login einmal auf deinem PC
erstellen (siehe unten) und die Datei `data/kleinanzeigen_state.json` auf den Server kopieren.

> Hinweis: Diese Claude-Code-Sitzung selbst ist nur eine temporäre Cloud-Umgebung und kann den Bot nicht dauerhaft
> betreiben – dafür brauchst du deinen PC oder einen eigenen Server.

### Nützliche Befehle
```bash
python -m ps5_scanner            # Dauerbetrieb
python -m ps5_scanner test       # Telegram-Testnachricht
python -m ps5_scanner once       # Einmal suchen & bewerten, Ausgabe im Terminal (zum Ausprobieren)
python -m ps5_scanner check URL  # Ein bestimmtes Inserat analysieren (inkl. KI + Nachrichtenentwurf)
python -m ps5_scanner login      # Kleinanzeigen-Login für das Anschreiben speichern
```

---

## 3. Verkäufer automatisch anschreiben

1. `pip install playwright && playwright install chromium` (bei `start_windows.bat` schon erledigt)
2. `python -m ps5_scanner login` → es öffnet sich ein Browser → bei Kleinanzeigen einloggen → im Terminal Enter.
3. In `config.yaml`: `messenger.enabled: true` (und ggf. `signature`).
4. Neustarten. In Telegram bei einem Deal **✉️ Anschreiben** → du siehst eine **Vorschau** der Nachricht
   (von der KI passend zum Inserat formuliert, inkl. Preisvorschlag bei VB) → **✅ Jetzt senden** oder **✏️ Eigener Text**.

Ohne eingerichteten Messenger bekommst du den fertigen Text zum Kopieren plus Link zum Inserat.
Schutz vor Spam-Sperren: max. 15 Nachrichten/Stunde (einstellbar). Wenn Kleinanzeigen sein Layout ändert und das
Senden scheitert, landet ein Screenshot in `data/screenshots/`.

---

## 4. Telegram-Befehle

| Befehl | Funktion |
|---|---|
| `/status` | Laufzeit, letzte erfolgreiche Suche, Statistik der letzten 24 h |
| `/pause`, `/weiter` | Meldungen anhalten / fortsetzen (Scanner läuft weiter) |
| `/preise` | Aktuelle Wiederverkaufswerte inkl. gelernter Marktpreise |
| `/top` | Die letzten 10 gemeldeten Deals |

---

## 5. Feinjustierung (`config.yaml`)

- **Mehr/weniger Meldungen:** `deal.min_profit` (Standard 60 €) und `deal.candidate_score` (Standard 40).
- **Nur in deiner Nähe:** auf kleinanzeigen.de mit PLZ + Umkreis suchen, URL in `scan.queries` eintragen.
- **Deine Reparatur-Skills:** unter `deal.repair` – z. B. Laufwerk auf `true`, wenn du das auch kannst.
- **Schneller:** `scan.interval_seconds` z. B. 12. Unter ~10 s steigt das Risiko, kurzzeitig gesperrt zu werden
  (der Bot erkennt das, pausiert automatisch und warnt dich in Telegram).
- **KI-Kosten:** Die KI prüft nur Kandidaten, die den Regel-Filter bestehen (typisch 20–80 pro Tag).
  Mit `claude-opus-5-5` (Standard, beste Einschätzung) grob 2–5 Cent pro Prüfung inkl. Bildern;
  `ai.model: claude-haiku-5-5` ist deutlich günstiger und etwas schneller, aber weniger gründlich.

---

## 6. Wie der Gewinn berechnet wird

```
Gewinn = Wiederverkaufswert(Modell) + Extras (OVP, 2. Controller, Garantie …)
       − Kaufpreis − Teilekosten (je Defekt) − Nebenkosten (15 €)
```
Teilekosten (Standard): Software 0 €, Wärmeleitpaste/Flüssigmetall 15 €, Lüfter 20 €, HDMI-Buchse 12 €,
Netzteil 45 €, USB 8 €, fehlender Controller 45 €. „Defekt“ ohne Ursache wird mit 40 € und erhöhtem Risiko angesetzt.

Typische Symptome → Ursache (so bewertet auch die KI):
- „Geht an, aber kein Bild“ → HDMI-Buchse (manchmal Retimer-Chip)
- „Geht nicht mehr an / kein Piepen“ → oft Netzteil (seltener Kurzschluss auf dem Board)
- „Schaltet sich beim Spielen ab / sehr laut“ → Thermik, Flüssigmetall verlaufen, Lüfter
- „Abgesicherter Modus / Update-Fehler“ → Firmware neu aufspielen

---

## 7. Wichtig zu wissen

- Das Abfragen von Kleinanzeigen und automatisierte Nachrichten verstoßen ggf. gegen deren Nutzungsbedingungen. Der Bot
  fragt maßvoll ab und begrenzt Nachrichten – trotzdem kann Kleinanzeigen dein Konto oder deine IP einschränken.
  Nutzung auf eigenes Risiko; schick keine Massen-Nachrichten.
- Die KI- und Regel-Bewertungen sind Schätzungen. Vor dem Kauf immer selbst Bilder/Beschreibung prüfen und bei
  Versand nur sichere Zahlungswege nutzen (Kleinanzeigen „Sicher bezahlen“, keine PayPal-Freunde-Zahlung an Fremde).

---

## Projektstruktur

```
ps5_scanner/
  app.py          Hauptschleife, Deal-Pipeline, Telegram-Buttons & Befehle, Morgen-Überblick
  scraper.py      HTTP-Zugriff, Backoff bei Sperren
  parser.py       Trefferliste & Detailseite parsen
  analyzer.py     Regel-Engine: Relevanz, Modell, Defekte, No-Gos, Betrug, Gewinn, Score
  market.py       Lernt Wiederverkaufswerte aus aktuellen Inseraten
  ai_filter.py    Claude-Bewertung (Text + Bilder, strukturierte Antwort)
  telegram_bot.py Telegram-Bot-API: Nachrichten, Buttons, Long-Polling
  messenger.py    Verkäufer anschreiben per Browser-Automatisierung (Playwright)
  schedule.py     Ruhezeiten
  storage.py      SQLite (gesehene Inserate, Marktpreise, Kontakte)
tests/            pytest-Tests (python -m pytest)
```
