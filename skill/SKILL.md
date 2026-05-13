---
name: talent-sourcer
description: Source 10 candidates for a job posting from public web sources via the GitHub REST API and Google web search (Stack Overflow, HackerNews, personal sites). Streams each candidate into candidates.csv as it's confirmed via the add_candidate tool, so partial progress survives any interruption. Use when given a job description text or a public job posting URL. Does not query LinkedIn.
---

# Talent Sourcer

Source **exactly 10 candidates** for the job. Call `add_candidate` once per candidate as you confirm them — partial progress is durably written to disk after each call.

**Never query LinkedIn.** Its pages are blocked from automated fetch and don't yield useful data.

## Available tools

- `fetch_company_team_page` (custom) — given a company name + homepage URL, tries common team-page paths (`/team`, `/about`, `/people`, etc.) and returns the page text. **Highest-leverage tool for non-engineering roles** at specific companies — one call often yields 5–20 candidates.
- `github_search_users` (custom) — direct GitHub REST API. **First-line tool for engineering roles.**
- `github_user_network` (custom) — given a GitHub username, returns their 1-hop follow graph (20 peer candidates by default). Snowball after `github_search_users` finds a strong seed.
- `add_candidate` (custom) — **append one ranked candidate to candidates.csv.** Call EXACTLY ONCE per candidate as soon as you confirm they fit. Do NOT batch. Each call is persisted before returning. Watch the response's `complete` field; when `true`, STOP.
- `web_search` — Google. Use for finding companies, personal sites, Stack Overflow, HackerNews, dev.to, conference speakers, portfolios. **Start broad** (2-4 words, no `site:` filters).
- `web_fetch` — pull full page contents from a known URL.
- `write`, `read`, `bash`, `edit`, `glob`, `grep` — container filesystem. **Do NOT use `write` for candidates.csv** — that's the orchestrator's job via `add_candidate`.

## Steps

1. **Get the job description.**
   - If the input URL is on `linkedin.com`, do NOT call `web_fetch` — go straight to `web_search` for a public mirror (Indeed, Glassdoor, careers page).
   - For non-LinkedIn URLs, try `web_fetch` at most 2 times.
   - **If after 2 web_fetch attempts plus 1 fallback web_search you don't have the JD text, STOP IMMEDIATELY.** Output one final message:
     > "Could not retrieve the job description from the URL. Please re-run with: `python sourcer.py --file role.txt` after pasting the JD text into `role.txt`."

     Do NOT call any more tools. Do NOT call `add_candidate`. Do NOT invent a JD.

2. **Parse the role.** Extract: title, seniority, 3–5 must-have skills, location, remote policy, deal-breakers.

3. **Source candidates — pick the right strategy for the role.**

   For each candidate you confirm, call `add_candidate` immediately with what you know. Empty strings for unverified fields. Watch the `complete` field — when `true`, STOP everything.

   The biggest mistake the agent has historically made is over-constrained Google queries that return 0 hits. **Don't do that.** Use the patterns below — they're the patterns a human sourcer actually uses.

   ### Pick a primary strategy

   | Role type | Primary strategy |
   |---|---|
   | Software engineer, data scientist, ML, DevOps, security | **A. GitHub-first** — `github_search_users` → `github_user_network` |
   | Chief of Staff, Founders Associate, Ops, Strategy, PM at specific companies | **B. Team-page first** — find 3-5 similar companies, then `fetch_company_team_page` each |
   | Designer, marketing, writer, generalist startup roles | **C. Portfolio-site first** — `web_search` Dribbble / Behance / Medium / Substack / personal sites |

   ### Pattern A — GitHub-first (engineering roles)

   1. Run **1 broad `github_search_users` query** mixing 1 language + 1 location filter. Example: `language:python location:"San Francisco" followers:>50`.
   2. Identify the **1-2 strongest seeds** in the result (rich profile, good repos, matches the role).
   3. Call `github_user_network` on each seed. Returns ~20 peers — often very high signal.
   4. Score every promising user and `add_candidate` them.
   5. If still under target, run **one more** `github_search_users` query varying the skill (e.g. `language:rust` if the role wants both).

   ### Pattern B — Team-page first (non-engineering at specific companies)

   This is the highest-leverage pattern for roles like "Chief of Staff at a Stuttgart robotics startup". A human sourcer would not search Google for individuals — they'd find the companies, then look at each company's team page.

   1. **Find 3–5 similar companies.** ONE broad `web_search`:
      - `Stuttgart robotics startups 2024`
      - `Berlin AI startups team`
      - `Munich climate-tech companies`

      The result is a list of companies. Extract 3–5 names.
   2. **For each company, find its homepage URL.** Either it's already in the search result, or a quick `web_search` like `Sereact official site` gets it.
   3. **Call `fetch_company_team_page(company_name, homepage_url)`** for each. Returns the team page text with 5–20 names and titles.
   4. **Extract candidates** from the page text. For each strong match, score and `add_candidate` directly (the team page itself is a verified public source).
   5. If the page is JS-heavy and `fetch_company_team_page` fails or returns thin text, use `web_fetch` on the same URL as a fallback (Anthropic's fetcher handles JavaScript).

   ### Pattern C — Portfolio-first (design, marketing, writers)

   1. ONE broad `web_search` per platform:
      - `site:dribbble.com <city>` for designers
      - `site:behance.net <skill> <city>`
      - `site:medium.com "<role>" <city>` for writers/PMs
      - `site:substack.com "<role>"`
   2. Open promising profiles via `web_fetch`. Each portfolio usually has the person's name, location, and recent work.
   3. Score and `add_candidate`.

   ### Query construction rules (when you use `web_search`)

   Google ranks **pages**, not paragraphs. The more constraints you AND together, the more likely you get zero hits.

   - **Start broad.** First query for any role should be 2-4 words with zero `site:` filters. Look at what comes back.
   - **Narrow ONE step at a time.** Add a location, OR a skill, OR a site filter — never multiple at once.
   - **If you get 0 hits, BROADEN — don't add more constraints.** The query was already too narrow.
   - **At most ONE `site:` qualifier per query.** Never `site:X OR site:Y` — split into two separate queries.
   - **At most TWO quoted phrases per query.**
   - **Don't combine `OR` with `site:`.** `"X" OR "Y" site:medium.com` returns almost nothing.

   #### Good vs bad queries

   | Bad (over-constrained → 0 hits) | Good (broad → real results) |
   |---|---|
   | `"chief of staff" OR "founders associate" tech startup Germany Stuttgart blog site:medium.com OR site:substack.com` | `"chief of staff" Stuttgart startup` |
   | `Sereact robotics Stuttgart team "strategy" OR "operations" employee profile` | `Sereact official site` (then `fetch_company_team_page`) |
   | `"founders associate" "operations" "strategy" "Stuttgart" Germany 2025` | `Stuttgart startups team page` (then fetch each) |
   | `site:dribbble.com "product designer" "Berlin" "fintech" 5+ years` | `site:dribbble.com Berlin product designer` |

   ### Search budgets (orchestrator-enforced)

   You have hard caps on tool calls. Going over either gets you a stop-message or a hard-refused error:

   | Tool | Cap | Enforcement |
   |---|---|---|
   | `web_search` | 4 | Soft (stop-message after the 4th) |
   | `web_fetch` | 6 | Soft |
   | `github_search_users` | 3 | Hard (refused) |
   | `github_user_network` | 2 | Hard (refused) |
   | `fetch_company_team_page` | 5 | Hard (refused) |

   Plan your moves with this in mind. If a search returns 0 hits, do NOT spend another budget slot on a tiny variation — switch tools or broaden.

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
