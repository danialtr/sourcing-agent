from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

from nurse_sourcer.extractors.contact import extract_contacts
from nurse_sourcer.extractors.signals import extract_signals
from nurse_sourcer.models import JobBrief, Query, RawHit
from nurse_sourcer.sources.base import Source

log = logging.getLogger(__name__)


class YouTube(Source):
    name = "youtube"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
        if not api_key or api_key.startswith("AIzaxxx"):
            raise RuntimeError(
                "YOUTUBE_API_KEY is missing. Create a project at "
                "https://console.cloud.google.com/ and enable YouTube Data API v3."
            )
        self.api_key = api_key
        self.videos_per_query = int(self.config.get("videos_per_query", 25))
        self.comments_per_video = int(self.config.get("comments_per_video", 100))

    async def run(self, queries: list[Query], brief: JobBrief) -> list[RawHit]:
        try:
            from googleapiclient.discovery import build  # type: ignore[import-not-found]
        except Exception as exc:
            log.warning("google-api-python-client missing; skipping YouTube: %s", exc)
            return []

        my_queries = self.relevant_queries(queries)
        if not my_queries:
            return []

        loop = asyncio.get_event_loop()
        youtube = build("youtube", "v3", developerKey=self.api_key, cache_discovery=False)

        all_hits: list[RawHit] = []

        def _find_videos(term: str) -> list[str]:
            try:
                resp = (
                    youtube.search()
                    .list(
                        q=term,
                        part="snippet",
                        type="video",
                        maxResults=min(self.videos_per_query, 50),
                        relevanceLanguage="en",
                    )
                    .execute()
                )
                return [
                    it["id"]["videoId"]
                    for it in resp.get("items", [])
                    if it.get("id", {}).get("videoId")
                ]
            except Exception as exc:
                log.warning("YouTube search failed for %r: %s", term, exc)
                return []

        def _video_meta(video_id: str) -> dict | None:
            try:
                resp = (
                    youtube.videos()
                    .list(id=video_id, part="snippet,statistics")
                    .execute()
                )
                items = resp.get("items", [])
                return items[0] if items else None
            except Exception as exc:
                log.warning("YouTube video meta failed for %s: %s", video_id, exc)
                return None

        def _comments(video_id: str) -> list[dict]:
            try:
                resp = (
                    youtube.commentThreads()
                    .list(
                        videoId=video_id,
                        part="snippet",
                        maxResults=min(self.comments_per_video, 100),
                        textFormat="plainText",
                    )
                    .execute()
                )
                return resp.get("items", []) or []
            except Exception as exc:
                # 403 commentsDisabled is common.
                log.info("Comments unavailable for %s: %s", video_id, exc)
                return []

        for q in my_queries:
            video_ids = await loop.run_in_executor(None, _find_videos, q.text)
            for vid in video_ids:
                meta = await loop.run_in_executor(None, _video_meta, vid)
                if meta:
                    snippet = meta.get("snippet", {})
                    desc = snippet.get("description", "") or ""
                    title = snippet.get("title", "") or ""
                    text = f"{title}\n{desc}"
                    all_hits.append(
                        RawHit(
                            source_name="youtube",
                            source_url=f"https://www.youtube.com/watch?v={vid}",
                            title=title,
                            text=text,
                            display_name=snippet.get("channelTitle"),
                            handle=snippet.get("channelTitle"),
                            contacts=extract_contacts(text),
                            signals=extract_signals(text),
                            extra={"type": "video", "channel_id": snippet.get("channelId")},
                        )
                    )

                comments = await loop.run_in_executor(None, _comments, vid)
                for c in comments:
                    sn = c.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
                    text = sn.get("textDisplay", "") or ""
                    author = sn.get("authorDisplayName")
                    channel_url = sn.get("authorChannelUrl", "")
                    contacts = extract_contacts(text, channel_url)
                    ts_str = sn.get("publishedAt")
                    when = (
                        datetime.fromisoformat(ts_str.replace("Z", "+00:00")).replace(tzinfo=None)
                        if ts_str
                        else datetime.utcnow()
                    )
                    all_hits.append(
                        RawHit(
                            source_name="youtube",
                            source_url=f"https://www.youtube.com/watch?v={vid}",
                            title=f"Comment on video {vid}",
                            text=text,
                            display_name=author,
                            handle=author,
                            contacts=contacts,
                            signals=extract_signals(text),
                            fetched_at=when,
                            extra={"type": "comment", "channel_url": channel_url},
                        )
                    )

        return all_hits
