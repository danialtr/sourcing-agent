# Talent Sourcing Agent

Single autonomous Claude Managed Agent that sources up to 50 candidates for a
job from **free public sources only**:

- **GitHub REST API** (best signal for engineering roles, via host-side custom tool)
- Google X-ray search of `linkedin.com/in`, `github.com`, `stackoverflow.com`
- Public job listings (Indeed, Glassdoor, company careers pages)

Writes `candidates.csv` with **full provenance** — every candidate row records
the exact query and URL the agent used to find them.

No Apollo, no Clay, no paid databases.

## Parts

| File | What it is |
|---|---|
| `skill/SKILL.md` | The agent's playbook — sourcing strategy, scoring, CSV format. Uploaded as a custom Anthropic skill. |
| `setup_agent.py` | One-time setup. Creates the cloud environment, uploads the skill, creates the agent (with the `github_search_users` custom tool declared). Saves IDs to `.env`. |
| `sourcer.py` | Runtime CLI. Creates a session, streams events, **executes custom tool calls host-side** (e.g. GitHub API), writes `provenance.jsonl`, downloads `candidates.csv`. |
| `.env` | Holds `ANTHROPIC_API_KEY`, `GITHUB_PAT`, and the IDs setup writes. |

The agent is configured with:
- Model: `claude-sonnet-4-6`
- Tools: `agent_toolset_20260401` (bash, read, write, edit, glob, grep, web_fetch, web_search) **+ `github_search_users` custom tool**
- Skill: `talent-sourcer`
- Environment: unrestricted cloud networking

## Why GitHub goes through a custom tool, not an MCP server

The agent could reach GitHub two ways:

1. **Hosted GitHub MCP** (`api.githubcopilot.com/mcp/`) — requires GitHub Copilot, an OAuth dance, and an Anthropic vault to store the token.
2. **Host-side custom tool** (this repo's choice) — agent emits `agent.custom_tool_use("github_search_users", ...)`; `sourcer.py` calls the GitHub REST API with your PAT and sends the result back. PAT never enters the agent's container.

Option 2 is simpler (PAT in `.env`, no OAuth, no vault), free, and gives us a
nice side-effect: every GitHub call goes through our orchestrator so we can log
it in the provenance audit trail.

## The 5 steps the agent runs autonomously

1. **Fetch the JD** — `web_fetch` the URL; fall back to `web_search` + fetch of public listings (Indeed / Glassdoor / careers page) if LinkedIn search pages return nothing useful.
2. **Parse the role** — title, seniority, must-have skills, location, remote policy, deal-breakers.
3. **Source candidates** — mix of:
   - `github_search_users` (custom tool, free GitHub REST API) — 2–3 queries for engineering roles
   - `web_search` X-ray (`site:linkedin.com/in`, etc.) — up to 6 queries
   - `web_fetch` to pull richer info from promising profiles
4. **Score 0–100** — must-have coverage weighted highest; deal-breakers lower the score and get flagged in `reason` rather than dropping the candidate.
5. **Write the CSV** to `/mnt/session/outputs/candidates.csv` with provenance columns.

## CSV columns

```
rank, match_score, name, current_title, current_company, location, email,
linkedin_url, profile_url_other, source, source_query, source_url, reason
```

- `source` — `linkedin`, `github`, `stackoverflow`, or `web`
- `source_query` — the exact search query or tool input that surfaced this candidate
- `source_url` — the page the agent extracted this candidate's info from

`email` will almost always be blank from public sources — public profiles rarely
expose it. The agent will not fabricate one.

## Provenance — knowing where every candidate came from

Three places to look:

1. **Live stdout** — every tool call is printed as it happens:
   ```
   [ 12.3s] web_search  query="site:linkedin.com/in \"Python\" \"San Francisco\""
   [ 13.1s]   -> (result): 8 results: jane-doe-engineer, john-smith-dev, ...
   [ 14.7s] github_search_users  query="language:python location:\"San Francisco\" followers:>50"
   [ 16.2s]   -> github_search_users: 18 users (jane123, alexr, bob-builder, ...)
   ```

2. **`provenance.jsonl`** — one JSON line per event (tool calls, results, errors, usage). Grep / `jq` it to audit any run after the fact.

3. **`source_query` + `source_url` columns** in the CSV — the per-candidate trail.

## How to run

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set credentials
cp .env.example .env
$EDITOR .env       # fill in ANTHROPIC_API_KEY and (optional) GITHUB_PAT

# 3. One-time agent setup
python setup_agent.py
# -> writes ENVIRONMENT_ID, SKILL_ID, AGENT_ID into .env

# 4. Run the agent on a job
python sourcer.py "https://www.linkedin.com/jobs/search/?currentJobId=4412749249&f_C=76732072&geoId=92000000"

# or with a local JD file
python sourcer.py --file role.txt
```

Outputs in your working directory:
- `candidates.csv` — the deliverable
- `provenance.jsonl` — every tool call + result + error + usage, one JSON object per line

## On Anthropic web tools vs raw `httpx`

| Target | Raw `httpx` | Anthropic `web_search` / `web_fetch` |
|---|---|---|
| **Google search** | Blocked (captcha) | Works — uses a paid search-API provider under the hood, not scraping |
| **LinkedIn profile pages** | Blocked (login wall) | Also mostly blocked — agent reads public Google snippets instead |
| **Indeed, Glassdoor, careers pages** | Hit-or-miss | Mostly works — headless Chromium, rotating IPs, JS execution |
| **GitHub, Stack Overflow, personal sites** | Works | Works | use httpx for free — this is what the GitHub custom tool does |

## Iterating

- **Tweak the playbook:** edit `skill/SKILL.md` → re-run `python setup_agent.py` → agent uses the new skill version on the next session, no agent recreation needed.
- **Add another custom tool** (e.g. Stack Exchange API, Hacker News): add the tool definition to `CUSTOM_TOOLS` in `setup_agent.py`, add a handler in `dispatch_custom_tool()` in `sourcer.py`, recreate the agent.
- **Change model / system prompt / built-in tools:** edit `setup_agent.py`, delete `AGENT_ID` from `.env`, re-run `setup_agent.py` — gives you a fresh agent with the new config.
