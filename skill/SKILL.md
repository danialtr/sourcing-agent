---
name: talent-sourcer
description: Source candidates for a job posting from public web sources via Google X-ray search and the GitHub API, and output a ranked candidates.csv with full provenance. Use when given a LinkedIn job posting URL or a job description text. Reads the role, runs targeted web searches plus GitHub user searches, ranks matches, and writes the CSV to /mnt/session/outputs/.
---

# Talent Sourcer

Source up to 50 candidates for a job using only public sources. Output a CSV with provenance so the user can audit where every candidate came from.

## Available tools

- `web_search`, `web_fetch` — generic public web (Google-indexed)
- `github_search_users` (custom) — direct GitHub REST API access. Prefer this over `site:github.com` web searches for engineering roles; results are typed and far richer (bio, location, company, public email if listed, follower count, repos)
- `write`, `read`, `bash`, `edit`, `glob`, `grep` — the container's filesystem

## Steps

1. **Get the job description.**
   - Try `web_fetch` on the input URL **at most 2 times**.
   - If the URL is for `linkedin.com` and `web_fetch` returns `url_not_allowed`, do NOT retry the same URL. Try **one** `web_search` to find a public mirror of the job posting (Indeed, Glassdoor, the company's careers page), then `web_fetch` that result.
   - **If after 2 `web_fetch` attempts plus 1 `web_search` you still don't have the JD text, STOP IMMEDIATELY.** Output one final message:
     > "Could not retrieve the job description from the URL (LinkedIn job-search URLs are commonly blocked). Please re-run with: `python sourcer.py --file role.txt` after pasting the JD text into `role.txt`."

     Do NOT call any more tools. Do NOT write an empty CSV. Do NOT invent a JD.

2. **Parse the role.** Extract: title, seniority, 3–5 must-have skills, location, remote policy, deal-breakers.

3. **Source candidates.** Aim for 50 unique candidates. Use a mix:
   - **For engineering roles, prefer `github_search_users`.** Run 2–3 queries varying the must-have skills and location. Example queries:
     - `language:python location:"San Francisco" followers:>50`
     - `language:rust location:"San Francisco"`
     - `fullname:"Jane Doe"` (for known-target lookups)
   - **Then `web_search` X-ray queries** to fill in non-engineering signals and find LinkedIn profiles:
     - `site:linkedin.com/in "<skill>" "<location>"`
     - `site:linkedin.com/in "<seniority> <skill>"`
     - `site:stackoverflow.com/users "<skill>"`
   - Cap total searches at **6 web_search + 3 github_search_users**. Stop earlier once you have 50 unique candidates.
   - Dedupe by LinkedIn URL, by GitHub login, or by normalized name + company.

4. **Score each candidate 0–100.** Weight must-have skill coverage highest; nice-to-have skills add bonus; flag deal-breakers in the `reason` field and apply a score penalty (do not drop the candidate). Write a one-sentence `reason`.

5. **Write `/mnt/session/outputs/candidates.csv`** with these columns in this exact order:

   ```
   rank,match_score,name,current_title,current_company,location,email,linkedin_url,profile_url_other,source,source_query,source_url,reason
   ```

   Column rules:
   - `rank` — 1..N, sorted by match_score descending
   - `email` — almost always blank from public sources. Only fill from a verified public source (GitHub public email field, candidate's own posted resume). Never guess.
   - `profile_url_other` — GitHub or Stack Overflow URL if found (LinkedIn goes in `linkedin_url`)
   - `source` — one of: `linkedin`, `github`, `stackoverflow`, `web`
   - `source_query` — **the exact search query or tool input** that found this candidate (e.g., `language:python location:"SF" followers:>50` or `site:linkedin.com/in "Python" "SF"`)
   - `source_url` — **the URL of the page** where you extracted this candidate's info (their LinkedIn profile, GitHub profile, or other source page)
   - Use standard CSV quoting (`"..."`) for any field containing a comma, quote, or newline

## Rules

- **Public sources only.** No paid databases, no scraping behind logins, no fabricating data.
- **Leave fields blank when uncertain.** Never guess emails, titles, or companies.
- **Aim for 50 rows; write fewer if that's all the searches found.** Do not pad with duplicates or low-confidence guesses.
- **Always fill `source_query` and `source_url`** — they are the user's audit trail.
