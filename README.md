# Talent Sourcing Agent

Single autonomous Claude Managed Agent that sources up to 50 candidates for a
job from **free public sources only** (Google X-ray search of `linkedin.com/in`,
`github.com`, `stackoverflow.com`, plus public job listings) and writes a
ranked `candidates.csv`.

No Apollo, no Clay, no paid databases.

## Parts

| File | What it is |
|---|---|
| `skill/SKILL.md` | The agent's playbook — sourcing strategy, scoring, CSV format. Uploaded as a custom Anthropic skill. |
| `setup_agent.py` | One-time setup. Creates the cloud environment, uploads the skill, creates the agent. Saves the IDs to `.env`. |
| `sourcer.py` | Runtime CLI. Creates a session, streams events, downloads `candidates.csv`. |
| `.env` | Holds `ANTHROPIC_API_KEY` and the IDs setup writes. |

The agent is configured with:
- Model: `claude-sonnet-4-6`
- Tools: `agent_toolset_20260401` (bash, read, write, edit, glob, grep, web_fetch, web_search)
- Custom skill: `talent-sourcer` (the file above)
- Environment: unrestricted cloud networking (so Google search results reach the container)

## Steps the agent runs (one autonomous session per CLI invocation)

1. **Fetch the JD.** `web_fetch` on the input URL; if it's a LinkedIn search page that returns nothing useful, `web_search` for the company + role and fetch a public listing (Indeed, Glassdoor, careers page).
2. **Parse the role.** Pulls out title, seniority, must-have skills, location, remote policy, deal-breakers.
3. **Source candidates.** Up to 6 `web_search` X-ray queries against `linkedin.com/in`, `github.com`, `stackoverflow.com`, etc. Dedupes by LinkedIn URL or name+company. Aims for 50 unique candidates, stops when it has them.
4. **Score 0–100.** Must-have coverage first; deal-breakers lower the score and get flagged in the `reason` field rather than dropping the candidate.
5. **Write the CSV.** `/mnt/session/outputs/candidates.csv`, sorted by score descending, ranked 1..N.

`sourcer.py` then downloads that file to your working directory as `candidates.csv`.

## How to run

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set up your API key
cp .env.example .env
$EDITOR .env       # fill in ANTHROPIC_API_KEY

# 3. One-time agent setup — creates environment + skill + agent
python setup_agent.py

# 4. Run the agent on a job
python sourcer.py "https://www.linkedin.com/jobs/search/?currentJobId=4412749249&f_C=76732072&geoId=92000000"

# Or with a local JD file
python sourcer.py --file role.txt
```

The agent's progress streams to your terminal as it works. When done, you'll
find `candidates.csv` in the current directory and a usage/cost summary printed
to stdout.

## CSV columns

```
rank, match_score, name, current_title, current_company, location, email, linkedin_url, profile_url_other, source, reason
```

`email` is almost always blank — public sources don't expose it. The agent
won't fabricate one.

## Iterating on the agent

- **Change the playbook:** edit `skill/SKILL.md`, then `python setup_agent.py` again — it uploads a new skill version, and the agent picks it up automatically.
- **Change the system prompt / tools / model:** edit `setup_agent.py`, delete `AGENT_ID` from `.env`, then `python setup_agent.py` — a fresh agent is created with the new config.
