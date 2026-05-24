from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from nurse_sourcer.extractors.contact import extract_contacts
from nurse_sourcer.extractors.signals import extract_signals
from nurse_sourcer.models import JobBrief, Query, RawHit
from nurse_sourcer.sources.base import Source, random_user_agent

log = logging.getLogger(__name__)


class Kalibrr(Source):
    name = "kalibrr"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.pages_per_keyword = int(self.config.get("pages_per_keyword", 3))

    async def run(self, queries: list[Query], brief: JobBrief) -> list[RawHit]:
        my_queries = self.relevant_queries(queries)
        if not my_queries:
            return []

        hits: list[RawHit] = []
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            for q in my_queries:
                for page in range(1, self.pages_per_keyword + 1):
                    url = (
                        f"https://www.kalibrr.com/home/te/{quote(q.text.replace(' ', '-'))}"
                        f"/co/ph/p/{page}"
                    )
                    try:
                        resp = await client.get(
                            url,
                            headers={"User-Agent": random_user_agent()},
                        )
                        if resp.status_code != 200:
                            log.info("Kalibrr %s -> %s", url, resp.status_code)
                            continue
                        hits.extend(self._parse(resp.text, url))
                    except Exception as exc:
                        log.warning("Kalibrr fetch failed: %s", exc)
                    await asyncio.sleep(2.5)
        return hits

    def _parse(self, html: str, url: str) -> list[RawHit]:
        soup = BeautifulSoup(html, "lxml")
        out: list[RawHit] = []
        for card in soup.select("a[href*='/c/'], div[class*='Job'], article"):
            text = card.get_text(" ", strip=True)
            if not text or len(text) < 30:
                continue
            href = card.get("href") or url
            if href and href.startswith("/"):
                href = f"https://www.kalibrr.com{href}"
            out.append(
                RawHit(
                    source_name="kalibrr",
                    source_url=href,
                    title=text[:120],
                    text=text,
                    contacts=extract_contacts(text, href),
                    signals=extract_signals(text),
                    extra={"type": "job_listing"},
                )
            )
        return out
