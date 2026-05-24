from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

from nurse_sourcer.extractors.contact import extract_contacts
from nurse_sourcer.extractors.signals import extract_signals
from nurse_sourcer.models import Contacts, JobBrief, Query, RawHit
from nurse_sourcer.sources.base import Source

log = logging.getLogger(__name__)


class RedditSearch(Source):
    name = "reddit"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        client_id = os.environ.get("REDDIT_CLIENT_ID", "").strip()
        client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "").strip()
        user_agent = os.environ.get("REDDIT_USER_AGENT", "").strip()
        if not client_id or not client_secret or not user_agent:
            raise RuntimeError(
                "REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET and REDDIT_USER_AGENT "
                "are required. Register a 'script' app at "
                "https://www.reddit.com/prefs/apps."
            )
        self.client_id = client_id
        self.client_secret = client_secret
        self.user_agent = user_agent
        self.posts_per_search = int(self.config.get("posts_per_search", 50))
        self.time_filter = self.config.get("time_filter", "year")

    async def run(self, queries: list[Query], brief: JobBrief) -> list[RawHit]:
        try:
            import praw  # type: ignore[import-not-found]
        except Exception as exc:
            log.warning("praw not installed; skipping Reddit: %s", exc)
            return []

        my_queries = self.relevant_queries(queries)
        if not my_queries:
            return []

        reddit = praw.Reddit(
            client_id=self.client_id,
            client_secret=self.client_secret,
            user_agent=self.user_agent,
            check_for_async=False,
        )

        loop = asyncio.get_event_loop()
        all_hits: list[RawHit] = []

        def _search(subreddit: str, term: str) -> list[RawHit]:
            hits: list[RawHit] = []
            try:
                sub = reddit.subreddit(subreddit)
                for post in sub.search(
                    term,
                    limit=self.posts_per_search,
                    time_filter=self.time_filter,
                ):
                    hits.append(_post_to_hit(post, subreddit))
                    # Skim top-level comments too (lightweight).
                    try:
                        post.comments.replace_more(limit=0)
                        for c in post.comments[:20]:
                            hits.append(_comment_to_hit(c, post, subreddit))
                    except Exception:
                        continue
            except Exception as exc:
                log.warning("Reddit search %s/%s failed: %s", subreddit, term, exc)
            return hits

        for q in my_queries:
            sub_name = q.subreddit or "all"
            try:
                results = await loop.run_in_executor(None, _search, sub_name, q.text)
                all_hits.extend(results)
            except Exception as exc:
                log.warning("Reddit task failed: %s", exc)

        return all_hits


def _post_to_hit(post, subreddit: str) -> RawHit:
    author = str(post.author) if post.author else None
    text = f"{post.title}\n{post.selftext or ''}"
    contacts = extract_contacts(text)
    if author and author.lower() not in {"automoderator", "[deleted]"}:
        contacts.reddit_usernames = list(
            dict.fromkeys(contacts.reddit_usernames + [author])
        )
    return RawHit(
        source_name="reddit",
        source_url=f"https://reddit.com{post.permalink}",
        title=post.title,
        text=text,
        display_name=author,
        handle=author,
        contacts=contacts,
        signals=extract_signals(text),
        fetched_at=datetime.utcfromtimestamp(post.created_utc),
        extra={"subreddit": subreddit, "type": "post", "score": post.score},
    )


def _comment_to_hit(comment, post, subreddit: str) -> RawHit:
    author = str(comment.author) if comment.author else None
    text = comment.body or ""
    contacts = extract_contacts(text)
    if author and author.lower() not in {"automoderator", "[deleted]"}:
        contacts.reddit_usernames = list(
            dict.fromkeys(contacts.reddit_usernames + [author])
        )
    return RawHit(
        source_name="reddit",
        source_url=f"https://reddit.com{comment.permalink}",
        title=f"Comment on: {post.title}",
        text=text,
        display_name=author,
        handle=author,
        contacts=contacts,
        signals=extract_signals(text),
        fetched_at=datetime.utcfromtimestamp(comment.created_utc),
        extra={"subreddit": subreddit, "type": "comment", "score": comment.score},
    )
