from __future__ import annotations

import asyncio
import logging

from nurse_sourcer.extractors.contact import extract_contacts
from nurse_sourcer.extractors.identity import guess_display_name
from nurse_sourcer.extractors.signals import extract_signals
from nurse_sourcer.models import JobBrief, Query, RawHit
from nurse_sourcer.sources.base import Source

log = logging.getLogger(__name__)


class DuckDuckGoSearch(Source):
    name = "duckduckgo"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.rate_limit = float(self.config.get("rate_limit_seconds", 2.5))
        self.max_results = int(self.config.get("max_results_per_query", 20))

    async def run(self, queries: list[Query], brief: JobBrief) -> list[RawHit]:
        try:
            from ddgs import DDGS  # type: ignore[import-not-found]
        except Exception as exc:
            log.warning("ddgs not importable; skipping DuckDuckGo: %s", exc)
            return []

        my_queries = self.relevant_queries(queries)
        hits: list[RawHit] = []
        loop = asyncio.get_event_loop()

        def _do_search(query: str) -> list[dict]:
            with DDGS() as client:
                return list(client.text(query, max_results=self.max_results)) or []

        for q in my_queries:
            try:
                results = await loop.run_in_executor(None, _do_search, q.text)
                hits.extend(self._results_to_hits(q.text, results))
            except Exception as exc:
                log.warning("DDG failed for %r: %s", q.text, exc)
            await asyncio.sleep(self.rate_limit)

        return hits

    def _results_to_hits(self, query: str, results: list[dict]) -> list[RawHit]:
        out: list[RawHit] = []
        for r in results:
            url = r.get("href") or r.get("url") or ""
            title = r.get("title") or ""
            body = r.get("body") or r.get("snippet") or ""
            text = f"{title}\n{body}"
            out.append(
                RawHit(
                    source_name="duckduckgo",
                    source_url=url,
                    title=title,
                    text=text,
                    display_name=guess_display_name(text),
                    contacts=extract_contacts(text, url),
                    signals=extract_signals(text),
                    extra={"query": query},
                )
            )
        return out
