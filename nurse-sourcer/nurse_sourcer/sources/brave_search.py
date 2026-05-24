from __future__ import annotations

import asyncio
import logging
import os
from urllib.parse import urlparse

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from nurse_sourcer.extractors.contact import extract_contacts
from nurse_sourcer.extractors.identity import guess_display_name
from nurse_sourcer.extractors.signals import extract_signals
from nurse_sourcer.models import JobBrief, Query, RawHit
from nurse_sourcer.sources.base import Source, random_user_agent

log = logging.getLogger(__name__)

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


class BraveSearch(Source):
    name = "brave"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        api_key = os.environ.get("BRAVE_API_KEY", "").strip()
        if not api_key or api_key.startswith("BSAxxx"):
            raise RuntimeError(
                "BRAVE_API_KEY is missing or unset. Sign up at "
                "https://api.search.brave.com/ and put the key in .env."
            )
        self.api_key = api_key
        self.rate_limit = float(self.config.get("rate_limit_seconds", 1.1))

    async def run(self, queries: list[Query], brief: JobBrief) -> list[RawHit]:
        my_queries = self.relevant_queries(queries)
        hits: list[RawHit] = []
        async with httpx.AsyncClient(timeout=20.0) as client:
            for q in my_queries:
                try:
                    results = await self._search(client, q.text)
                    hits.extend(self._results_to_hits(q.text, results))
                except Exception as exc:
                    log.warning("Brave search failed for %r: %s", q.text, exc)
                await asyncio.sleep(self.rate_limit)
        return hits

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=20))
    async def _search(self, client: httpx.AsyncClient, query: str) -> list[dict]:
        resp = await client.get(
            BRAVE_ENDPOINT,
            params={"q": query, "count": 20, "country": "ALL", "search_lang": "en"},
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self.api_key,
                "User-Agent": random_user_agent(),
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("web", {}).get("results", []) or []

    def _results_to_hits(self, query: str, results: list[dict]) -> list[RawHit]:
        out: list[RawHit] = []
        for r in results:
            url = r.get("url", "")
            title = r.get("title", "")
            desc = r.get("description", "")
            text = f"{title}\n{desc}"
            contacts = extract_contacts(text, url)
            signals = extract_signals(text)
            display_name = guess_display_name(text)
            handle = _handle_from_url(url)
            out.append(
                RawHit(
                    source_name="brave",
                    source_url=url,
                    title=title,
                    text=text,
                    display_name=display_name,
                    handle=handle,
                    contacts=contacts,
                    signals=signals,
                    extra={"query": query, "platform": _platform_of(url)},
                )
            )
        return out


def _platform_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return "web"
    for plat in (
        "linkedin.com",
        "facebook.com",
        "youtube.com",
        "tiktok.com",
        "reddit.com",
        "medium.com",
        "jobstreet.com",
        "kalibrr.com",
    ):
        if plat in host:
            return plat.split(".")[0]
    return "web"


def _handle_from_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return None
    if "linkedin.com" in parsed.netloc and "in" in parts:
        try:
            return parts[parts.index("in") + 1]
        except (ValueError, IndexError):
            return None
    if "youtube.com" in parsed.netloc:
        if parts[0].startswith("@"):
            return parts[0][1:]
        if parts[0] == "channel" and len(parts) > 1:
            return parts[1]
    if "tiktok.com" in parsed.netloc and parts[0].startswith("@"):
        return parts[0][1:]
    if "reddit.com" in parsed.netloc and parts[0] in ("u", "user") and len(parts) > 1:
        return parts[1]
    return None
