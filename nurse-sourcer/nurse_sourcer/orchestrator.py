from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import yaml
from dotenv import load_dotenv

from nurse_sourcer.dedup import deduplicate
from nurse_sourcer.models import JobBrief, Query, RawHit
from nurse_sourcer.query_builder import build_queries
from nurse_sourcer.scoring import passes_minimum_signal, score_lead
from nurse_sourcer.sources.base import Source
from nurse_sourcer.sources.brave_search import BraveSearch
from nurse_sourcer.sources.duckduckgo import DuckDuckGoSearch
from nurse_sourcer.sources.facebook_public import FacebookPublic
from nurse_sourcer.sources.goethe_manila import GoetheManila
from nurse_sourcer.sources.google_cse import GoogleCSE
from nurse_sourcer.sources.jobstreet_ph import JobstreetPH
from nurse_sourcer.sources.kalibrr import Kalibrr
from nurse_sourcer.sources.reddit_search import RedditSearch
from nurse_sourcer.sources.tiktok_hashtag import TikTokHashtag
from nurse_sourcer.sources.youtube import YouTube
from nurse_sourcer.storage import Store

log = logging.getLogger(__name__)


SOURCE_CLASSES: dict[str, type[Source]] = {
    "brave_search": BraveSearch,
    "duckduckgo": DuckDuckGoSearch,
    "reddit": RedditSearch,
    "youtube": YouTube,
    "facebook_public": FacebookPublic,
    "tiktok_hashtag": TikTokHashtag,
    "jobstreet_ph": JobstreetPH,
    "kalibrr": Kalibrr,
    "goethe_manila": GoetheManila,
    "google_cse": GoogleCSE,
}


def load_brief(path: Path) -> JobBrief:
    with path.open() as fh:
        data = yaml.safe_load(fh)
    return JobBrief.model_validate(data)


def load_sources_config(path: Path) -> dict:
    with path.open() as fh:
        data = yaml.safe_load(fh) or {}
    return data.get("sources", {})


def _instantiate_sources(sources_config: dict) -> list[Source]:
    """Each source is its own pipeline stage. Missing API keys raise
    loudly — we don't silently skip configured sources.
    """
    out: list[Source] = []
    for key, cls in SOURCE_CLASSES.items():
        cfg = sources_config.get(key, {})
        if not cfg.get("enabled", False if key == "google_cse" else True):
            log.info("Source %s is disabled — skipping.", key)
            continue
        out.append(cls(cfg))
    return out


async def _run_source(source: Source, queries: list[Query], brief: JobBrief) -> list[RawHit]:
    try:
        log.info("Running source: %s", source.name)
        hits = await source.run(queries, brief)
        log.info("Source %s returned %d raw hits", source.name, len(hits))
        return hits
    except Exception as exc:
        # Fragile sources (FB, TikTok) must not bring the run down.
        log.exception("Source %s crashed: %s", source.name, exc)
        return []


async def run_pipeline(
    brief_path: Path,
    sources_path: Path,
    db_path: Path,
    max_queries_override: int | None = None,
) -> dict:
    load_dotenv()
    brief = load_brief(brief_path)
    sources_config = load_sources_config(sources_path)

    queries = build_queries(brief, sources_config)
    if max_queries_override is not None:
        queries = queries[:max_queries_override]
    log.info("Built %d queries across all sources", len(queries))

    sources = _instantiate_sources(sources_config)

    tasks = [_run_source(s, queries, brief) for s in sources]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    all_hits: list[RawHit] = [hit for batch in results for hit in batch]
    log.info("Aggregated %d raw hits", len(all_hits))

    leads = deduplicate(all_hits)
    log.info("After dedup: %d unique leads", len(leads))

    # Score + persist (only leads that pass the minimum signal gate).
    accepted = 0
    with Store(db_path) as store:
        for lead in leads:
            if not passes_minimum_signal(lead):
                continue
            lead.score = score_lead(lead, brief)
            lead_id = store.upsert_lead(lead)
            for url in lead.source_urls[:5]:
                store.add_source(lead_id, lead.sources[0], url, "", lead.last_seen_at)
            accepted += 1

        for q in queries:
            store.log_query(q.text, q.source, 0, 0)

        stats = store.stats()

    log.info("Persisted %d leads (passed minimum signal).", accepted)
    return {
        "queries_built": len(queries),
        "raw_hits": len(all_hits),
        "unique_leads_pre_filter": len(leads),
        "stored_leads": accepted,
        "store_stats": stats,
    }
