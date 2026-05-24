# nurse-sourcer

A standalone Python CLI that, given a German hospital job brief, finds
Filipino registered nurses who appear interested in moving to Germany,
surfaces a contact path for each one, and produces a ranked CSV of leads.

**Constraints (hard):**

- $0/month operating cost. Only free tiers and open data.
- No LLM calls anywhere in the pipeline. All logic is rule-based and deterministic.
- No paid scraping providers.
- We only find + list. Outreach, messaging, CRM are out of scope.

## How it works

```
job_brief.yaml
      │
      ▼
query_builder ──► 10 sources in parallel ──► extractors ──► dedup ──► scoring ──► SQLite ──► CSV
                  (Brave, DDG, Reddit,        (regex)
                   YouTube, FB public,
                   TikTok, JobStreet,
                   Kalibrr, Goethe,
                   Google CSE optional)
```

Roughly 500 distinct queries / fetches per run, ~300-700 raw hits,
~20-60 with contact paths, ~5-15 high-quality leads.

## Setup

Requires Python 3.11+.

```bash
cd nurse-sourcer
uv sync                       # or: pip install -e .
playwright install chromium   # only needed if Facebook source is enabled
cp .env.example .env          # then fill in API keys
```

### API keys (all free)

- `BRAVE_API_KEY` — https://api.search.brave.com/ (2,000 queries/month free).
- `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` —
  register a "script" app at https://www.reddit.com/prefs/apps.
- `YOUTUBE_API_KEY` — Google Cloud Console, enable YouTube Data API v3
  (10,000 quota units/day free).
- `GOOGLE_CSE_API_KEY` and `GOOGLE_CSE_CX` — optional, only if you turn
  on the `google_cse` source in `config/sources.yaml`.

DuckDuckGo, public Facebook pages, TikTok, JobStreet, Kalibrr, and
Goethe Manila require no keys.

> Missing keys fail loudly at startup — there is no silent fallback.
> To skip a source, set `enabled: false` for it in `config/sources.yaml`.

## Usage

```bash
# Run the full sourcing job (~500 queries by default)
python -m nurse_sourcer run --brief config/job_brief.yaml

# Quick smoke test: 25 queries, faster, no Playwright
python -m nurse_sourcer run --max-queries 25

# Show DB stats and the top 10 leads
python -m nurse_sourcer stats

# Re-export the top 100 leads from the DB
python -m nurse_sourcer export --top 100 --output data/exports/leads_top100.csv
```

Output lands in `data/exports/leads_YYYY-MM-DD.csv`, sorted by score
descending. The SQLite DB at `data/leads.db` persists across runs so
duplicates are merged.

## The job brief

Canonical input format is `config/job_brief.yaml`. The shipped example
is a real-shape Leipzig ICU brief — edit it for a different role. The
schema is enforced by Pydantic in `nurse_sourcer/models.py`.

## Per-source config

`config/sources.yaml` controls which sources run and their per-source
limits (rate-limit seconds, page counts, subreddits, hashtags, etc.).
Most knobs you'd ever change live there, not in code.

## Scoring (0-100, rule-based, no LLM)

```
is_nurse              +20
is_filipino           +15
mentions_germany      +15
mentions_icu (if ICU) +10
mentions_language     +10
mentions_program      +10
years_experience ≥ min +5
has_contact_path      +10
was_active_recently   +5
```

A lead must have `is_nurse AND (is_filipino OR mentions_germany)` to
be stored at all. Everything else is noise.

## Tests

```bash
python -m pytest -q
```

Tests cover extractors, scoring, and dedup. No network in tests.

## Known limitations

1. Facebook scraping breaks frequently. It's wrapped in best-effort
   error handling — when it breaks, the rest of the system keeps working.
2. LinkedIn URLs are surfaced but not enriched. You get profile URLs
   to manually visit, not parsed data.
3. Filipino names with variants (Maria Cristina vs. Ma. Cristina) may
   sometimes be duplicated.
4. Contact paths are listed but not verified. A Reddit username is
   contactable; an email may bounce.
5. No phone numbers at scale — those are gated behind paid data providers.

## Project layout

```
nurse-sourcer/
├── pyproject.toml
├── .env.example
├── config/
│   ├── job_brief.yaml
│   └── sources.yaml
├── data/                       # SQLite + CSV exports (gitignored)
├── nurse_sourcer/
│   ├── main.py                 # CLI entry point
│   ├── orchestrator.py         # runs sources in parallel
│   ├── query_builder.py        # ~500 queries from the brief
│   ├── scoring.py
│   ├── dedup.py
│   ├── storage.py              # SQLite
│   ├── export.py               # CSV writer
│   ├── models.py               # Pydantic models
│   ├── extractors/             # regex contact + signal extractors
│   └── sources/                # 10 source modules
└── tests/
```
