---
name: talent-sourcer
description: Source candidates for a job posting from public web sources via Google X-ray search and output a ranked candidates.csv. Use when given a LinkedIn job posting URL or a job description text. Reads the role, runs targeted web searches across LinkedIn/GitHub/Stack Overflow public profiles, ranks matches, and writes the CSV to /mnt/session/outputs/.
---

# Talent Sourcer

Source up to 50 candidates for a job using only public web sources.

## Steps

1. **Get the job description.**
   - Try `web_fetch` on the input URL.
   - If it's a LinkedIn search/listing page that returns nothing useful, `web_search` for the company name + role title + location, then `web_fetch` the public listing on Indeed, Glassdoor, or the company careers page.
   - If still nothing, report the failure and stop — do not invent a JD.

2. **Parse the role.** Extract: title, seniority, 3–5 must-have skills, location and remote policy, deal-breakers.

3. **Source candidates via Google X-ray search.** Run at most 6 `web_search` queries. Vary them across these patterns, substituting the must-have skills and the location:
   - `site:linkedin.com/in "<skill>" "<location>"`
   - `site:linkedin.com/in "<seniority> <skill>"`
   - `site:github.com "<skill>" location:"<location>"`
   - `site:stackoverflow.com/users "<skill>"`
   - `"<skill>" "<location>" resume OR CV`

   For each result extract: name, current title, current company, location, profile URL. Dedupe by LinkedIn URL, or by normalized name + company when no LinkedIn URL is present. Aim for 50 unique candidates; stop searching once you have them.

4. **Score each candidate 0–100.** Weight must-have skill coverage highest; nice-to-have skills add bonus; flag deal-breakers in the `reason` field and apply a score penalty (do not drop the candidate). Write a one-sentence `reason`.

5. **Write `/mnt/session/outputs/candidates.csv`.** Use the `write` tool. Columns in this exact order:

   ```
   rank,match_score,name,current_title,current_company,location,email,linkedin_url,profile_url_other,source,reason
   ```

   - Sort rows by `match_score` descending, then assign `rank` 1..N.
   - `email` is almost always blank from public sources — leave it blank rather than guess.
   - `profile_url_other` is the GitHub or Stack Overflow URL if found (LinkedIn goes in `linkedin_url`).
   - `source` is `linkedin`, `github`, `stackoverflow`, or `web` depending on which site the candidate was found on.
   - Use standard CSV quoting (`"..."`) for any field containing a comma, quote, or newline.

## Rules

- **Public sources only.** No paid databases, no scraping behind logins, no fabricating data.
- **Leave fields blank when uncertain.** Never guess emails, titles, or companies.
- **Aim for 50 rows; write fewer if that's all the searches found.** Do not pad with duplicates or low-confidence guesses.
