from __future__ import annotations

import asyncio
import logging
import os

import httpx

from nurse_sourcer.extractors.contact import extract_contacts
from nurse_sourcer.extractors.identity import guess_display_name
from nurse_sourcer.extractors.signals import extract_signals
from nurse_sourcer.models import JobBrief, Query, RawHit
from nurse_sourcer.sources.base import Source, random_user_agent

log = logging.getLogger(__name__)

CSE_ENDPOINT = "https://www.googleapis.com/customsearch/v1"


class GoogleCSE(Source):
    """Optional backup search source via Google Programmable Search Engine.
    Disabled by default — turn on in config/sources.yaml when needed.
    """

    name = "google_cse"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.api_key = os.environ.get("GOOGLE_CSE_API_KEY", "").strip()
        self.cx = os.environ.get("GOOGLE_CSE_CX", "").strip()
        if not self.api_key or not self.cx:
            raise RuntimeError(
                "GOOGLE_CSE_API_KEY and GOOGLE_CSE_CX must be set when "
                "google_cse is enabled. Create an engine at "
                "https://programmablesearchengine.google.com/."
            )

    async def run(self, queries: list[Query], brief: JobBrief) -> list[RawHit]:
        my_queries = self.relevant_queries(queries)
        hits: list[RawHit] = []
        async with httpx.AsyncClient(timeout=15.0) as client:
            for q in my_queries:
                try:
                    resp = await client.get(
                        CSE_ENDPOINT,
                        params={"key": self.api_key, "cx": self.cx, "q": q.text, "num": 10},
                        headers={"User-Agent": random_user_agent()},
                    )
                    if resp.status_code != 200:
                        log.info("Google CSE %r -> %s", q.text, resp.status_code)
                        continue
                    for item in resp.json().get("items", []):
                        url = item.get("link", "")
                        title = item.get("title", "") or ""
                        snippet = item.get("snippet", "") or ""
                        text = f"{title}\n{snippet}"
                        hits.append(
                            RawHit(
                                source_name="google_cse",
                                source_url=url,
                                title=title,
                                text=text,
                                display_name=guess_display_name(text),
                                contacts=extract_contacts(text, url),
                                signals=extract_signals(text),
                                extra={"query": q.text},
                            )
                        )
                except Exception as exc:
                    log.warning("Google CSE failed for %r: %s", q.text, exc)
                await asyncio.sleep(1.0)
        return hits
