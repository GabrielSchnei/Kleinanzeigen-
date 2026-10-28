"""HTTP-Zugriff auf Kleinanzeigen mit Keep-Alive, Backoff und Sperr-Erkennung."""

from __future__ import annotations

import asyncio
import logging
import random
import time

import httpx

from .models import Listing
from .parser import parse_detail_page, parse_search_page

log = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
]


class BlockedError(Exception):
    """Kleinanzeigen antwortet mit 403/429 – wir müssen eine Pause einlegen."""


class Scraper:
    def __init__(self, max_concurrent: int = 4):
        self.client = httpx.AsyncClient(
            headers={
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.5",
                "Cache-Control": "no-cache",
            },
            timeout=httpx.Timeout(10.0, connect=5.0),
            follow_redirects=True,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=10),
        )
        self.sem = asyncio.Semaphore(max_concurrent)
        self.blocked_until = 0.0
        self.block_count = 0

    async def close(self) -> None:
        await self.client.aclose()

    async def _get(self, url: str) -> httpx.Response:
        wait = self.blocked_until - time.time()
        if wait > 0:
            raise BlockedError(f"Pause wegen Sperre, noch {int(wait)} s")
        async with self.sem:
            resp = await self.client.get(url)
        if resp.status_code in (403, 429):
            self.block_count += 1
            backoff = min(60 * 2 ** (self.block_count - 1), 1800)
            self.blocked_until = time.time() + backoff
            raise BlockedError(f"HTTP {resp.status_code} – Pause {backoff} s")
        if resp.status_code < 400:
            self.block_count = 0
        return resp

    async def search(self, url: str, query_name: str) -> list[Listing]:
        resp = await self._get(url)
        resp.raise_for_status()
        listings = parse_search_page(resp.text, query_name)
        if not listings and "captcha" in resp.text.lower():
            self.block_count += 1
            self.blocked_until = time.time() + 300
            raise BlockedError("Captcha-Seite erhalten – Pause 300 s")
        return listings

    async def enrich(self, listing: Listing) -> Listing:
        """Lädt die Detailseite. Gelöschte Inserate werden als `gone` markiert."""
        resp = await self._get(listing.url)
        if resp.status_code in (404, 410):
            listing.gone = True
            listing.detail_loaded = True
            return listing
        resp.raise_for_status()
        return parse_detail_page(resp.text, listing)
