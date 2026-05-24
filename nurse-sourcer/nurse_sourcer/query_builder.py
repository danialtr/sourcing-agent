from __future__ import annotations

from nurse_sourcer.models import JobBrief, Query


def build_queries(brief: JobBrief, sources_config: dict) -> list[Query]:
    """Build all queries for one run from the brief and per-source config.

    No LLM. Pure string templating from the YAML. Aim ~500 distinct
    queries / fetches across all sources combined.
    """
    queries: list[Query] = []

    role_terms = ["nurse", "RN", "registered nurse"]
    role_terms += [a for a in brief.role.specialty_aliases[:3]]
    nat_terms = brief.target_source.country_aliases[:4] or [brief.target_source.country]
    intent_terms = [
        "Germany",
        "Deutschland",
        "Pflegefachkraft",
        "Anerkennung",
        "Triple Win",
    ]
    lang_terms = [
        brief.requirements.german_level_at_application,
        brief.requirements.german_level_required_within_12_months,
        "Goethe",
    ]

    brave_budget = brief.search_settings.brave_search_budget
    brave_cfg = sources_config.get("brave_search", {})
    if brave_cfg.get("enabled", True):
        brave_max = min(brave_budget, brave_cfg.get("max_queries", 100))
        brave_queries: list[str] = []

        # Combinatorial — broad coverage of the target signal.
        for role in role_terms[:3]:
            for nat in nat_terms[:3]:
                for intent in intent_terms[:4]:
                    brave_queries.append(f'"{nat}" "{role}" "{intent}"')

        # Specialty-anchored.
        for nat in nat_terms[:2]:
            for spec in brief.role.specialty_aliases[:3] + [brief.role.specialty]:
                brave_queries.append(f'"{nat}" nurse "{spec}" Germany')

        # Language gate.
        for nat in nat_terms[:2]:
            for lang in lang_terms[:3]:
                brave_queries.append(f'"{nat}" nurse "{lang}" Deutsch')

        # Site-restricted long tail.
        for site, extra in [
            ("linkedin.com/in", '"ICU nurse" "Philippines" "Germany"'),
            ("linkedin.com/in", '"Pflegefachkraft" Filipino'),
            ("reddit.com", '"Filipino nurse" "B1" Germany'),
            ("reddit.com", '"Filipino nurse" "Triple Win"'),
            ("facebook.com", '"Triple Win" Philippines'),
            ("facebook.com", '"Filipino nurse" Germany'),
            ("medium.com", "Filipino nurse Germany journey"),
            ("youtube.com", "Filipino nurse Germany vlog"),
            ("tiktok.com", "filipino nurse germany"),
        ]:
            brave_queries.append(f"site:{site} {extra}")

        # Trim to budget.
        for q in brave_queries[:brave_max]:
            queries.append(Query(text=q, source="brave"))

    # DuckDuckGo — overlap + extra long tail.
    ddg_cfg = sources_config.get("duckduckgo", {})
    if ddg_cfg.get("enabled", True):
        ddg_max = ddg_cfg.get("max_queries", 150)
        ddg_queries: list[str] = []
        for role in role_terms[:3]:
            for nat in nat_terms[:3]:
                for intent in intent_terms:
                    ddg_queries.append(f'"{nat}" "{role}" "{intent}"')
        for spec in brief.role.specialty_aliases:
            ddg_queries.append(f'Filipino nurse {spec} Germany')
        for site in ["reddit.com", "facebook.com", "medium.com", "youtube.com"]:
            ddg_queries.append(f"site:{site} Filipino nurse Germany")
        for q in ddg_queries[:ddg_max]:
            queries.append(Query(text=q, source="duckduckgo"))

    # Reddit — search inside each target subreddit.
    reddit_cfg = sources_config.get("reddit", {})
    if reddit_cfg.get("enabled", True):
        for sub in reddit_cfg.get("subreddits", []):
            for term in [
                "Filipino nurse Germany",
                "Pinoy nurse Germany",
                "Triple Win",
                "Anerkennung nurse",
                f"nurse {brief.requirements.german_level_at_application} Germany",
            ]:
                queries.append(
                    Query(text=term, source="reddit", subreddit=sub)
                )

    # YouTube — video search queries; comment fetches happen per video downstream.
    youtube_cfg = sources_config.get("youtube", {})
    if youtube_cfg.get("enabled", True):
        for term in youtube_cfg.get("video_search_queries", []):
            queries.append(Query(text=term, source="youtube"))

    # TikTok hashtags.
    tiktok_cfg = sources_config.get("tiktok_hashtag", {})
    if tiktok_cfg.get("enabled", True):
        for tag in tiktok_cfg.get("hashtags", []):
            queries.append(Query(text=tag, source="tiktok"))

    # Facebook public pages.
    fb_cfg = sources_config.get("facebook_public", {})
    if fb_cfg.get("enabled", True):
        for page in fb_cfg.get("pages", []):
            queries.append(Query(text=page, source="facebook"))

    # JobStreet / Kalibrr.
    js_cfg = sources_config.get("jobstreet_ph", {})
    if js_cfg.get("enabled", True):
        for kw in js_cfg.get("keywords", []):
            queries.append(Query(text=kw, source="jobstreet"))

    kal_cfg = sources_config.get("kalibrr", {})
    if kal_cfg.get("enabled", True):
        for kw in kal_cfg.get("keywords", []):
            queries.append(Query(text=kw, source="kalibrr"))

    # Goethe Manila (single landing fetch).
    gm_cfg = sources_config.get("goethe_manila", {})
    if gm_cfg.get("enabled", True):
        queries.append(Query(text="goethe-manila-events", source="goethe"))

    # Google CSE — optional.
    gcse_cfg = sources_config.get("google_cse", {})
    if gcse_cfg.get("enabled", False):
        for q in [
            'site:linkedin.com/in "ICU nurse" Philippines Germany',
            'site:linkedin.com/in Pflegefachkraft Filipino',
            'site:reddit.com Filipino nurse B2 Germany',
        ]:
            queries.append(Query(text=q, source="google_cse"))

    return queries
