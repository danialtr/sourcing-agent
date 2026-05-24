from __future__ import annotations

import asyncio
import logging

from nurse_sourcer.extractors.contact import extract_contacts
from nurse_sourcer.extractors.signals import extract_signals
from nurse_sourcer.models import Contacts, JobBrief, Query, RawHit
from nurse_sourcer.sources.base import Source

log = logging.getLogger(__name__)


class TikTokHashtag(Source):
    """Hashtag-page scrape via yt-dlp. Surfaces video creator handles and
    titles. We don't fetch full video metadata to keep this cheap.
    """

    name = "tiktok"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.videos_per_hashtag = int(self.config.get("videos_per_hashtag", 30))

    async def run(self, queries: list[Query], brief: JobBrief) -> list[RawHit]:
        try:
            from yt_dlp import YoutubeDL  # type: ignore[import-not-found]
        except Exception as exc:
            log.warning("yt-dlp missing; skipping TikTok: %s", exc)
            return []

        my_queries = self.relevant_queries(queries)
        if not my_queries:
            return []

        loop = asyncio.get_event_loop()
        ydl_opts = {
            "quiet": True,
            "skip_download": True,
            "extract_flat": True,
            "playlistend": self.videos_per_hashtag,
            "ignoreerrors": True,
        }

        all_hits: list[RawHit] = []

        def _extract(tag: str) -> list[dict]:
            url = f"https://www.tiktok.com/tag/{tag}"
            try:
                with YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if not info:
                        return []
                    entries = info.get("entries") or []
                    return [e for e in entries if e]
            except Exception as exc:
                log.warning("TikTok hashtag #%s failed: %s", tag, exc)
                return []

        for q in my_queries:
            tag = q.text.lstrip("#")
            entries = await loop.run_in_executor(None, _extract, tag)
            for e in entries:
                title = e.get("title") or ""
                uploader = e.get("uploader") or e.get("channel") or ""
                webpage = e.get("webpage_url") or e.get("url") or f"https://www.tiktok.com/tag/{tag}"
                text = f"{title}\n#{tag}"
                contacts = extract_contacts(text, webpage)
                if uploader:
                    contacts.tiktok_handles = list(
                        dict.fromkeys(contacts.tiktok_handles + [uploader.lstrip("@")])
                    )
                all_hits.append(
                    RawHit(
                        source_name="tiktok",
                        source_url=webpage,
                        title=title,
                        text=text,
                        display_name=uploader,
                        handle=uploader.lstrip("@") if uploader else None,
                        contacts=contacts,
                        signals=extract_signals(text),
                        extra={"hashtag": tag},
                    )
                )

        return all_hits
