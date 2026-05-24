from __future__ import annotations

import asyncio
import logging

from nurse_sourcer.extractors.contact import extract_contacts
from nurse_sourcer.extractors.identity import guess_display_name
from nurse_sourcer.extractors.signals import extract_signals
from nurse_sourcer.models import JobBrief, Query, RawHit
from nurse_sourcer.sources.base import Source, random_user_agent

log = logging.getLogger(__name__)


class FacebookPublic(Source):
    """Public-page-only Facebook scrape. No login. Best effort: if Meta
    blocks, the rest of the pipeline keeps running.
    """

    name = "facebook"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.delay = float(self.config.get("delay_seconds", 10))
        self.posts_per_page = int(self.config.get("posts_per_page", 10))

    async def run(self, queries: list[Query], brief: JobBrief) -> list[RawHit]:
        try:
            from playwright.async_api import async_playwright  # type: ignore[import-not-found]
        except Exception as exc:
            log.warning("Playwright missing; skipping Facebook: %s", exc)
            return []

        my_queries = self.relevant_queries(queries)
        if not my_queries:
            return []

        hits: list[RawHit] = []
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                context = await browser.new_context(
                    user_agent=random_user_agent(),
                    viewport={"width": 1280, "height": 800},
                    locale="en-US",
                )
                for q in my_queries:
                    page_handle = q.text
                    try:
                        hits.extend(
                            await self._scrape_page(context, page_handle)
                        )
                    except Exception as exc:
                        log.warning("Facebook page %s failed: %s", page_handle, exc)
                    await asyncio.sleep(self.delay)
                await browser.close()
        except Exception as exc:
            log.warning("Facebook scrape aborted: %s", exc)

        return hits

    async def _scrape_page(self, context, page_handle: str) -> list[RawHit]:
        url = f"https://www.facebook.com/{page_handle}"
        page = await context.new_page()
        out: list[RawHit] = []
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            # Try a few scrolls to load posts.
            for _ in range(3):
                await page.mouse.wheel(0, 2000)
                await asyncio.sleep(2)
            content = await page.content()
            text = await page.evaluate("() => document.body.innerText")
            contacts = extract_contacts(text or "", url)
            signals = extract_signals(text or "")
            out.append(
                RawHit(
                    source_name="facebook",
                    source_url=url,
                    title=f"FB page: {page_handle}",
                    text=(text or "")[:5000],
                    display_name=page_handle,
                    handle=page_handle,
                    contacts=contacts,
                    signals=signals,
                    extra={"type": "page"},
                )
            )
            # Also try to extract any names that look like commenters from the raw HTML.
            for name in _names_from_html(content):
                t = name
                out.append(
                    RawHit(
                        source_name="facebook",
                        source_url=url,
                        title=f"Commenter on {page_handle}",
                        text=t,
                        display_name=guess_display_name(t) or name,
                        contacts=extract_contacts(t, url),
                        signals=extract_signals(t),
                        extra={"type": "commenter", "page": page_handle},
                    )
                )
        finally:
            await page.close()
        return out


def _names_from_html(html: str) -> list[str]:
    """Very loose heuristic: pull `aria-label` strings and any obvious
    Filipino name candidates out of the raw HTML. Meta's markup changes
    constantly — keep this defensive.
    """
    import re

    candidates: list[str] = []
    for m in re.finditer(r'aria-label="([^"]{3,60})"', html):
        val = m.group(1).strip()
        if " " in val and val.replace(" ", "").isalpha():
            candidates.append(val)
    return list(dict.fromkeys(candidates))[:30]
