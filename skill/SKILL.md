---
name: talent-sourcer
description: Source candidates for a job posting from public web sources via the GitHub REST API and Google web search (Stack Overflow, HackerNews, personal sites, conference talks). Outputs a ranked candidates.csv with full provenance. Use when given a job description text or a public job posting URL. Does not query LinkedIn.
---

# Talent Sourcer

Source up to 50 candidates for a job using public sources only.

**Never query LinkedIn.** LinkedIn profile pages are blocked from automated fetch (`url_not_allowed`) and its search snippets don't yield useful candidate data. Spend your search budget on sources that actually work.

## Available tools

- `github_search_users` (custom) — direct GitHub REST API. **First-line tool for engineering roles.** Returns typed profile data: login, name, bio, location, company, public email (if listed), blog URL, follower count, repo count.
- `web_search` — Google. Use for Stack Overflow, HackerNews, dev.to, Medium, Dribbble, conference speakers, personal portfolios.
- `web_fetch` — pull full page contents from non-LinkedIn URLs (company careers pages, personal sites, GitHub repos, conference pages).
- `write`, `read`, `bash`, `edit`, `glob`, `grep` — container filesystem.

## Steps

1. **Get the job description.**
   - If the input URL is on `linkedin.com`, **do NOT call web_fetch on it.** Go straight to a `web_search` for the company name + job title + location to find a public mirror (Indeed, Glassdoor, the company's careers page).
   - For non-LinkedIn URLs, try `web_fetch` at most 2 times.
   - **If after 2 `web_fetch` attempts plus 1 fallback `web_search` you don't have the JD text, STOP IMMEDIATELY.** Output one final message:
     > "Could not retrieve the job description from the URL. Please re-run with: `python sourcer.py --file role.txt` after pasting the JD text into `role.txt`."

     Do NOT call any more tools. Do NOT write an empty CSV. Do NOT invent a JD.

2. **Parse the role.** Extract: title, seniority, 3–5 must-have skills, location, remote policy, deal-breakers.

3. **Source candidates.** Aim for 50 unique candidates. Stop earlier once you have them.

   **For engineering roles**, lead with the GitHub API:
   - `github_search_users` with 2–3 queries varying skills and location:
     - `language:python location:"San Francisco" followers:>50`
     - `language:rust location:"San Francisco"`
     - `language:typescript location:"Berlin" repos:>10`

   Then supplement with web_search across non-LinkedIn channels:
   - `site:stackoverflow.com/users "<skill>"` — high-rep Q&A users
   - `site:news.ycombinator.com "<skill>" "<location>"` — HN bios in profile pages
   - `site:dev.to "<skill>"` — devs who write
   - `"<skill>" "<location>" "resume" OR "portfolio"` — personal sites
   - `"speaker" "<skill> conference"` — senior speakers (for senior roles)

   **For non-engineering roles** (PM, design, marketing, sales): use web_search of personal portfolios, Medium, Substack, Dribbble (designers), Behance, Notion bio pages, conference talk lists.

   Cap total searches at **3 `github_search_users` + 6 `web_search`**. Dedupe by GitHub login or by normalized name + company.

4. **Score each candidate 0–100.** Weight must-have skill coverage highest; nice-to-have skills add bonus; flag deal-breakers in the `reason` field and apply a score penalty (do not drop the candidate).

5. **Write `/mnt/session/outputs/candidates.csv`** with these columns in this exact order:

   ```
   rank,match_score,name,current_title,current_company,location,email,profile_url,source,source_query,source_url,reason
   ```

   Column rules:
   - `rank` — 1..N, sorted by `match_score` descending
   - `email` — only fill from a verified public source (GitHub public email field, candidate's own posted resume). Never guess.
   - `profile_url` — the candidate's primary public profile URL (GitHub, Stack Overflow, personal site, Dribbble, etc.). Whichever is most informative.
   - `source` — one of: `github`, `stackoverflow`, `hackernews`, `devto`, `web`
   - `source_query` — the exact search query or tool input that surfaced this candidate
   - `source_url` — the URL of the page the agent extracted this candidate's info from
   - Use standard CSV quoting (`"..."`) for any field containing a comma, quote, or newline

## Rules

- **Public sources only. No LinkedIn. No paid databases. No fabricated data.**
- Leave fields blank when uncertain. Never guess emails, titles, or companies.
- Aim for 50 rows; write fewer if that's all the searches found. Do not pad with duplicates or low-confidence guesses.
- Always fill `source_query` and `source_url` — they are the user's audit trail.
