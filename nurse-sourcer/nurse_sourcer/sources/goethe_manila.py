from __future__ import annotations

import asyncio
import logging

import httpx
from bs4 import BeautifulSoup

from nurse_sourcer.extractors.contact import extract_contacts
from nurse_sourcer.extractors.signals import extract_signals
from nurse_sourcer.models import JobBrief, Query, RawHit
from nurse_sourcer.sources.base import Source, random_user_agent

log = logging.getLogger(__name__)

GOETHE_URLS = [
    "https://www.goethe.de/ins/ph/en/sta/man.html",
    "https://www.goethe.de/ins/ph/en/spr.html",
    "https://www.goethe.de/ins/ph/en/ver.html",  # events page
]


class GoetheManila(Source):
    """Language-gate filter: anyone studying German in Manila is a
    serious emigration candidate. We extract events, partners, contacts.
    """

    name = "goethe"

    async def run(self, queries: list[Query], brief: JobBrief) -> list[RawHit]:
        my_queries = self.relevant_queries(queries)
        if not my_queries:
            return []

        hits: list[RawHit] = []
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            for url in GOETHE_URLS:
                try:
                    resp = await client.get(
                        url,
                        headers={"User-Agent": random_user_agent()},
                    )
                    if resp.status_code != 200:
                        log.info("Goethe %s -> %s", url, resp.status_code)
                        continue
                    hits.extend(self._parse(resp.text, url))
                except Exception as exc:
                    log.warning("Goethe fetch %s failed: %s", url, exc)
                await asyncio.sleep(2.0)
        return hits

    def _parse(self, html: str, url: str) -> list[RawHit]:
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)
        out: list[RawHit] = []
        out.append(
            RawHit(
                source_name="goethe",
                source_url=url,
                title=soup.title.string if soup.title and soup.title.string else "Goethe Manila",
                text=text[:5000],
                contacts=extract_contacts(text, url),
                signals=extract_signals(text),
                extra={"type": "landing"},
            )
        )
        # Pull every link to an event/news page so callers see what's running.
        for link in soup.select("a[href*='/ver/'], a[href*='/spr/'], a[href*='events']"):
            href = link.get("href") or ""
            if href.startswith("/"):
                href = f"https://www.goethe.de{href}"
            lt = link.get_text(" ", strip=True)
            if not lt or len(lt) < 6:
                continue
            out.append(
                RawHit(
                    source_name="goethe",
                    source_url=href,
                    title=lt[:120],
                    text=lt,
                    contacts=extract_contacts(lt, href),
                    signals=extract_signals(lt),
                    extra={"type": "event_link"},
                )
            )
        return out
