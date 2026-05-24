from __future__ import annotations

import asyncio
import random
from abc import ABC, abstractmethod

from nurse_sourcer.models import JobBrief, Query, RawHit

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


def random_user_agent() -> str:
    return random.choice(USER_AGENTS)


async def jittered_sleep(base: float, jitter: float = 0.5) -> None:
    await asyncio.sleep(base + random.uniform(0, jitter))


class Source(ABC):
    """Abstract base for a data source. Sources are best-effort:
    if one fails, the rest of the pipeline keeps running.
    """

    name: str = "source"

    def __init__(self, config: dict) -> None:
        self.config = config or {}

    @abstractmethod
    async def run(
        self, queries: list[Query], brief: JobBrief
    ) -> list[RawHit]:
        ...

    def relevant_queries(self, queries: list[Query]) -> list[Query]:
        return [q for q in queries if q.source == self.name]
