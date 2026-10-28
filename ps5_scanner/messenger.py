"""Verkäufer automatisch anschreiben – über einen echten (Headless-)Browser mit deinem Kleinanzeigen-Login.

Ablauf:
1. Einmalig `python -m ps5_scanner login` auf einem Rechner mit Bildschirm ausführen, im Browserfenster
   bei Kleinanzeigen einloggen, im Terminal Enter drücken. Die Sitzung wird in `storage_state` gespeichert.
2. Danach kann der Bot (auch headless auf einem Server) über den Telegram-Button Nachrichten senden.

Kleinanzeigen kann das Layout ändern – wenn das Senden scheitert, legt der Bot einen Screenshot
unter data/screenshots/ ab.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from .config import Config

log = logging.getLogger(__name__)

LOGIN_URL = "https://www.kleinanzeigen.de/m-einloggen.html"
CONSENT_SELECTORS = ["#gdpr-banner-accept", "button:has-text('Alle akzeptieren')", "button:has-text('Akzeptieren')"]
CONTACT_BUTTONS = [
    "#viewad-contact-button",
    "button:has-text('Nachricht schreiben')",
    "a:has-text('Nachricht schreiben')",
    "button:has-text('Nachricht senden')",
]
MESSAGE_FIELDS = ["#viewad-contact-message", "textarea[name='message']", "textarea"]
SUBMIT_BUTTONS = [
    "#viewad-contact-modal-form button[type='submit']",
    "#viewad-contact-form button[type='submit']",
    "form:has(textarea[name='message']) button[type='submit']",
    "button:has-text('Nachricht senden')",
]
SUCCESS_SELECTORS = [".j-viewad-contact-success:visible", "text=Nachricht wurde", "text=Nachricht gesendet",
                     "text=erfolgreich gesendet"]


class MessengerError(Exception):
    pass


class Messenger:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._pw = None
        self._browser = None
        self._context = None
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return self.cfg.messenger_enabled and Path(self.cfg.messenger_storage_state).exists()

    async def _ensure_browser(self):
        if self._context is not None:
            return self._context
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            raise MessengerError("Playwright ist nicht installiert: pip install playwright && playwright install chromium") from e
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.cfg.messenger_headless)
        self._context = await self._browser.new_context(
            storage_state=self.cfg.messenger_storage_state, locale="de-DE",
            viewport={"width": 1280, "height": 900},
        )
        return self._context

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()
        self._context = self._browser = self._pw = None

    @staticmethod
    async def _first_visible(page, selectors: list[str], timeout_ms: int = 4000):
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            for sel in selectors:
                loc = page.locator(sel).first
                try:
                    if await loc.count() and await loc.is_visible():
                        return loc
                except Exception:
                    continue
            await asyncio.sleep(0.2)
        return None

    async def send(self, ad_url: str, text: str) -> str:
        """Sendet `text` an den Verkäufer. Gibt eine Erfolgsmeldung zurück oder wirft MessengerError."""
        if not self.cfg.messenger_enabled:
            raise MessengerError("Messenger ist in config.yaml deaktiviert (messenger.enabled).")
        if not Path(self.cfg.messenger_storage_state).exists():
            raise MessengerError("Kein Login gespeichert – zuerst `python -m ps5_scanner login` ausführen.")

        async with self._lock:
            try:
                context = await self._ensure_browser()
                page = await context.new_page()
            except MessengerError:
                raise
            except Exception as e:
                await self.close()
                raise MessengerError(f"Browser konnte nicht gestartet werden: {e}") from e
            try:
                await page.goto(ad_url, wait_until="domcontentloaded", timeout=20000)
                consent = await self._first_visible(page, CONSENT_SELECTORS, 1500)
                if consent is not None:
                    await consent.click()

                if await page.locator("#viewad-contact-button-login").count() and \
                        await page.locator("#viewad-contact-button-login").first.is_visible():
                    raise MessengerError("Nicht eingeloggt – Login abgelaufen. Bitte `python -m ps5_scanner login` erneut ausführen.")
                if await page.locator("text=Diese Anzeige ist nicht mehr verfügbar").count():
                    raise MessengerError("Anzeige ist nicht mehr verfügbar.")

                field = await self._first_visible(page, MESSAGE_FIELDS, 1500)
                if field is None:
                    button = await self._first_visible(page, CONTACT_BUTTONS, 5000)
                    if button is None:
                        raise MessengerError("Kontakt-Button nicht gefunden.")
                    await button.click()
                    field = await self._first_visible(page, MESSAGE_FIELDS, 6000)
                if field is None:
                    raise MessengerError("Nachrichtenfeld nicht gefunden.")
                await field.fill(text)

                name_field = page.locator("#contact-contactName-input").first
                if self.cfg.messenger_contact_name and await name_field.count() and await name_field.is_visible():
                    if not (await name_field.input_value()).strip():
                        await name_field.fill(self.cfg.messenger_contact_name)

                submit = await self._first_visible(page, SUBMIT_BUTTONS, 3000)
                if submit is None:
                    raise MessengerError("Senden-Button nicht gefunden.")
                await submit.click()

                success = await self._first_visible(page, SUCCESS_SELECTORS, 12000)
                if success is None:
                    error = page.locator(".j-viewad-contact-error .ajaxform-error, .formerror:visible").first
                    detail = (await error.inner_text()).strip() if await error.count() else "keine Bestätigung erhalten"
                    raise MessengerError(f"Senden unklar: {detail}")

                await context.storage_state(path=self.cfg.messenger_storage_state)  # Cookies auffrischen
                return "Nachricht gesendet ✅"
            except MessengerError:
                await self._screenshot(page)
                raise
            except Exception as e:
                await self._screenshot(page)
                raise MessengerError(f"Browserfehler: {e}") from e
            finally:
                await page.close()

    @staticmethod
    async def _screenshot(page) -> None:
        try:
            Path("data/screenshots").mkdir(parents=True, exist_ok=True)
            path = f"data/screenshots/contact_{int(time.time())}.png"
            await page.screenshot(path=path, full_page=True)
            log.info("Screenshot gespeichert: %s", path)
        except Exception:
            pass


async def interactive_login(cfg: Config) -> None:
    """Öffnet ein sichtbares Browserfenster zum manuellen Einloggen und speichert die Sitzung."""
    from playwright.async_api import async_playwright

    Path(cfg.messenger_storage_state).parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context(locale="de-DE")
        page = await context.new_page()
        await page.goto(LOGIN_URL)
        print("\n👉 Bitte im Browserfenster bei Kleinanzeigen einloggen (inkl. evtl. Captcha / 2FA).")
        await asyncio.get_running_loop().run_in_executor(None, input, "   Danach hier Enter drücken … ")
        await context.storage_state(path=cfg.messenger_storage_state)
        await browser.close()
    print(f"✅ Login gespeichert in {cfg.messenger_storage_state}")
    print("   Setze in config.yaml `messenger.enabled: true`, damit der Telegram-Button Nachrichten sendet.")
