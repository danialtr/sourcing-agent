---
name: talent-sourcer
description: Source 10 candidates for a job posting from public web sources via the GitHub REST API and Google web search (Stack Overflow, HackerNews, personal sites). Streams each candidate into candidates.csv as it's confirmed via the add_candidate tool, so partial progress survives any interruption. Use when given a job description text or a public job posting URL. Does not query LinkedIn.
---

# Talent Sourcer

Source **exactly 10 candidates** for the job. Call `add_candidate` once per candidate as you confirm them — partial progress is durably written to disk after each call.

**Never query LinkedIn.** Its pages are blocked from automated fetch and don't yield useful data.

## Available tools

- `github_search_users` (custom) — direct GitHub REST API. First-line tool for engineering roles.
- `add_candidate` (custom) — **append one ranked candidate to candidates.csv.** Call this EXACTLY ONCE per candidate as soon as you confirm they fit. Do NOT batch. Each call is persisted before returning, so the run is crash-safe. Watch the `count` / `remaining` / `complete` fields in the response — when `complete: true`, STOP.
- `web_search` — Google. Use for `site:stackoverflow.com/users`, `site:news.ycombinator.com`, `site:dev.to`, conference speakers, personal portfolios.
- `web_fetch` — pull full page contents from non-LinkedIn URLs.
- `write`, `read`, `bash`, `edit`, `glob`, `grep` — container filesystem. **Do NOT use `write` for candidates.csv** — that's the orchestrator's job via `add_candidate`.

## Steps

1. **Get the job description.**
   - If the input URL is on `linkedin.com`, do NOT call `web_fetch` — go straight to `web_search` for a public mirror (Indeed, Glassdoor, careers page).
   - For non-LinkedIn URLs, try `web_fetch` at most 2 times.
   - **If after 2 web_fetch attempts plus 1 fallback web_search you don't have the JD text, STOP IMMEDIATELY.** Output one final message:
     > "Could not retrieve the job description from the URL. Please re-run with: `python sourcer.py --file role.txt` after pasting the JD text into `role.txt`."

     Do NOT call any more tools. Do NOT call `add_candidate`. Do NOT invent a JD.

2. **Parse the role.** Extract: title, seniority, 3–5 must-have skills, location, remote policy, deal-breakers.

3. **Source candidates one at a time.**
   - For each search query (GitHub or web), examine each result.
   - For each strong candidate, score them 0–100 against the must-haves and call `add_candidate` immediately with all the fields you know. Use empty strings for fields you can't verify — **never guess emails, titles, or companies**.
   - After each `add_candidate` call, check the response's `complete` field. When it's `true`, STOP — do not call `add_candidate` again, do not run more searches.
   - If a candidate is a duplicate, `add_candidate` returns `{"duplicate": true}` and the candidate is silently skipped — just continue to the next candidate.

   **For engineering roles**, lead with the GitHub API. Run 1–2 `github_search_users` queries varying skills and location:
   - `language:python location:"San Francisco" followers:>50`
   - `language:rust location:"San Francisco"`

   Then if you need more candidates, supplement with web_search:
   - `site:stackoverflow.com/users "<skill>"`
   - `site:news.ycombinator.com "<skill>" "<location>"`
   - `site:dev.to "<skill>"`
   - `"<skill>" "<location>" "resume" OR "portfolio"`

   **For non-engineering roles** (PM, design, marketing): use web_search of personal portfolios, Medium, Substack, Dribbble, Behance, conference talks.

   ### Search budget — STRICT

   - **At most 2 `github_search_users` and 4 `web_search` calls total per run.** The orchestrator enforces this — if you exceed it, you'll receive a `user.message` telling you to stop and the next web_search calls will be wasted.
   - **If a search returns 0 hits, DO NOT retry with a small variation.** That's a sign the query is over-constrained. Either:
     - Drop one constraint and try again (only if you haven't hit the budget), OR
     - Switch to `github_search_users`, OR
     - Stop and call `add_candidate` with the candidates you already have.

   ### Query construction rules — follow these

   Google ranks **pages**, not paragraphs. The more constraints you AND together, the more likely you get zero hits.

   - **One concept per query.** A skill OR a location OR a site — not all three combined with quotes and OR.
   - **At most ONE `site:` qualifier per query.** Never combine `site:X OR site:Y` — split into two separate queries.
   - **At most TWO quoted phrases per query.** Each `"..."` is a hard constraint Google must satisfy exactly.
   - **Don't combine `OR` with `site:`.** `"X" OR "Y" site:medium.com` returns almost nothing.
   - **For tiny companies / very specific employee searches, use `github_search_users` instead.** Google has almost no signal on "find me employees of <50-person startup>".

   #### Good vs bad queries (study these)

   | Bad (over-constrained) | Good (focused) |
   |---|---|
   | `"chief of staff" OR "founders associate" tech startup Germany Stuttgart blog site:medium.com OR site:substack.com` | `"chief of staff" Stuttgart startup` |
   | `Sereact robotics Stuttgart team "strategy" OR "operations" employee profile` | `language:typescript location:"Stuttgart"` (github_search_users instead) |
   | `site:linkedin.com/in "Senior Python" "Berlin" "5 years experience"` | `site:stackoverflow.com/users "python" "Berlin"` |
   | `"founders associate" "operations" "strategy" "Stuttgart" Germany 2025` | `"founders associate" Stuttgart` |

   Dedupe by GitHub login or by normalized name + company.

4. **Done.** After `add_candidate` returns `complete: true`, output one brief summary message listing the 10 names and stop. The orchestrator handles final sorting and ranking — do not call `write` on candidates.csv.

## Field guidance for `add_candidate`

- `name` — full name (required)
- `match_score` — 0–100 against must-haves (required)
- `current_title`, `current_company`, `location` — empty string if not publicly stated
- `email` — empty string unless publicly listed on GitHub or candidate's own resume; **never guess**
- `profile_url` — GitHub, Stack Overflow, personal site, etc. — whichever is the candidate's primary public presence
- `source` — `github`, `stackoverflow`, `hackernews`, `devto`, or `web`
- `source_query` — the EXACT search query or tool input that surfaced this candidate
- `source_url` — the URL of the page where you extracted the data
- `reason` — one sentence on why they match; flag any deal-breakers here

## Rules

- **Public sources only. No LinkedIn. No paid databases. No fabricated data.**
- Add candidates ONE AT A TIME via `add_candidate` — do NOT batch.
- Stop as soon as the tool reports `complete: true`.
- Leave fields blank when uncertain. Never guess.
