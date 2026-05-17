"""One-time setup for the Talent Sourcer agent.

Creates (or reuses) a cloud environment, uploads the talent-sourcer skill,
and creates the agent. Saves the resulting IDs to .env so sourcer.py can
find them later.

Run again any time you change skill/SKILL.md — it'll create a new skill
version and the agent will pick it up automatically (since the skill is
referenced without a pinned version).
"""

from __future__ import annotations

import os
import pathlib
import sys

import anthropic
from dotenv import load_dotenv, set_key

ROOT = pathlib.Path(__file__).parent
ENV_PATH = ROOT / ".env"
SKILL_PATH = ROOT / "skill" / "SKILL.md"

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = (
    "You are a talent sourcing agent. When given a job posting URL or a job "
    "description, source candidates from public web sources and write each "
    "one immediately to candidates.csv using the `add_candidate` tool.\n\n"
    "Follow the talent-sourcer skill for the sourcing process.\n\n"
    "There is NO scoring. For each person you find who plausibly fits the "
    "role (based on their public profile / team page / etc.), call "
    "`add_candidate` once with a one-sentence reason explaining why. Do not "
    "rate them, do not assign a number — just decide fit and move on.\n\n"
    "CRITICAL: After confirming a candidate fits the role, call `add_candidate` "
    "EXACTLY ONCE for that candidate before moving on. Do NOT batch — each "
    "call durably persists the candidate to disk so partial progress survives "
    "interruptions. Stop after the tool tells you the target count is reached. "
    "Do NOT write candidates.csv with the `write` tool — the orchestrator "
    "handles that.\n\n"
    "Sourcing tools you have, in rough priority order:\n"
    "  - `fetch_company_team_page` — best for non-engineering roles at "
    "specific companies (5-20 candidates from one team page)\n"
    "  - `github_search_users` — best first move for engineering roles\n"
    "  - `github_user_network` — snowball from one good GitHub user to peers\n"
    "  - `web_search` — fallback for non-engineering when you don't have a "
    "company yet; START BROAD (2-4 words, no site: filters), narrow only "
    "if you get too many results. If a query returns 0 hits, BROADEN — "
    "never add more constraints.\n"
    "  - `web_fetch` — pull a specific page once you have a URL\n\n"
    "**Do NOT query LinkedIn** — its pages are blocked from automated fetch. "
    "Never fabricate data — leave a field blank if you cannot find it from "
    "public sources."
)

# Custom tools executed host-side by sourcer.py. The agent emits
# agent.custom_tool_use; the orchestrator calls the underlying API and sends
# back a user.custom_tool_result. The credentials never enter the container.
CUSTOM_TOOLS = [
    {
        "type": "custom",
        "name": "github_search_users",
        "description": (
            "Search GitHub users by location, language, name, bio, follower "
            "count, or other criteria via the GitHub REST API. Returns up to "
            "30 users, each with login, name, bio, location, company, public "
            "email (if listed), blog URL, follower count, and public repo "
            "count. Prefer this over `site:github.com` web searches when "
            "sourcing engineers — the data is typed and far richer. "
            "Query syntax follows https://docs.github.com/en/search-github/"
            "searching-on-github/searching-users — examples: "
            "'language:python location:\"San Francisco\" followers:>50', "
            "'fullname:\"Jane Doe\"', 'language:rust followers:>100'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "GitHub user search query. Use GitHub's search "
                        "syntax (language:, location:, followers:, etc.)."
                    ),
                },
                "per_page": {
                    "type": "integer",
                    "description": "Max results to return (1-30, default 20).",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 30,
                },
            },
            "required": ["query"],
        },
    },
    {
        "type": "custom",
        "name": "github_user_network",
        "description": (
            "Get a GitHub user's 1-hop social graph: who they follow and who "
            "follows them, with full profile data on each connected user. "
            "Powerful 'snowball sampling' tool — once `github_search_users` "
            "finds one strong engineering candidate, calling this with their "
            "login often surfaces 10–20 peers in the same scene who would "
            "otherwise take many wasted web_search calls to find. Returns "
            "each user's login, name, bio, location, company, public email "
            "(if listed), blog, follower count, repo count, and whether they "
            "follow the seed or are followed by the seed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {
                    "type": "string",
                    "description": "GitHub login (e.g. 'jane123'), without @ or URL.",
                },
                "include_following": {
                    "type": "boolean",
                    "description": "Include users the seed follows.",
                    "default": True,
                },
                "include_followers": {
                    "type": "boolean",
                    "description": "Include users who follow the seed.",
                    "default": True,
                },
                "max_users": {
                    "type": "integer",
                    "description": "Cap total users returned (1-100, default 20).",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 100,
                },
            },
            "required": ["username"],
        },
    },
    {
        "type": "custom",
        "name": "fetch_company_team_page",
        "description": (
            "Try common team-page URL patterns at a company's homepage and "
            "return the first hit as plain text. Patterns tried: /team, "
            "/about, /about-us, /people, /company/team, /our-team, "
            "/careers/team, etc. **Highest-leverage tool for sourcing "
            "non-engineering candidates** at specific companies — a single "
            "team page often lists 5–20 employees with names and titles, "
            "saving many wasted web_search calls. Host-side (free, does not "
            "use the web_fetch budget). Requires the company's homepage URL; "
            "if you don't know it, run a web_search first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company_name": {
                    "type": "string",
                    "description": "Company display name, for logging/output.",
                },
                "homepage_url": {
                    "type": "string",
                    "description": (
                        "Full URL of the company's homepage (e.g. "
                        "'https://sereact.ai'). Required — if you don't know "
                        "it, run a web_search like '<company> official site' first."
                    ),
                },
            },
            "required": ["company_name", "homepage_url"],
        },
    },
    {
        "type": "custom",
        "name": "add_candidate",
        "description": (
            "Append ONE candidate to candidates.csv. Call this exactly once "
            "per candidate as soon as you confirm they fit the role — do NOT "
            "batch. Each call is durably written to disk before returning, "
            "so partial progress survives any crash. The response tells you "
            "the running count and remaining slots; stop calling this tool "
            "once `complete: true` is returned. Duplicates (same name + "
            "profile_url) are silently skipped. There is no scoring — just "
            "decide whether the candidate is a fit and write a one-sentence "
            "reason."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Candidate's full name."},
                "current_title": {
                    "type": "string",
                    "description": "Current job title, or empty string if unknown.",
                },
                "current_company": {
                    "type": "string",
                    "description": "Current employer, or empty string if unknown.",
                },
                "location": {
                    "type": "string",
                    "description": "City/country, or empty string if unknown.",
                },
                "email": {
                    "type": "string",
                    "description": (
                        "Public email (GitHub's email field, posted resume). "
                        "Empty string if not publicly listed — NEVER guess."
                    ),
                },
                "profile_url": {
                    "type": "string",
                    "description": (
                        "Primary public profile URL (GitHub, Stack Overflow, "
                        "personal site, etc.). Empty if not available."
                    ),
                },
                "source": {
                    "type": "string",
                    "enum": ["github", "stackoverflow", "hackernews", "devto", "web"],
                    "description": "Which kind of source surfaced this candidate.",
                },
                "source_query": {
                    "type": "string",
                    "description": (
                        "The exact search query or tool input that found this "
                        "candidate (e.g. 'language:python location:\"SF\" "
                        "followers:>50')."
                    ),
                },
                "source_url": {
                    "type": "string",
                    "description": (
                        "URL of the page where you extracted this candidate's "
                        "info (their GitHub profile, SO user page, etc.)."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "One sentence on why this person fits the role. "
                        "Mention deal-breakers if any (e.g. 'Strong Python "
                        "background but located in São Paulo, may not "
                        "relocate')."
                    ),
                },
            },
            "required": [
                "name", "source", "source_query", "source_url", "reason",
            ],
        },
    },
]


def _ensure_env_file() -> None:
    if not ENV_PATH.exists():
        ENV_PATH.touch()


def _get_or_create_environment(client: anthropic.Anthropic) -> str:
    env_id = os.getenv("ENVIRONMENT_ID")
    if env_id:
        try:
            client.beta.environments.retrieve(env_id)
            print(f"  using existing environment: {env_id}")
            return env_id
        except anthropic.NotFoundError:
            print(f"  stored ENVIRONMENT_ID {env_id} no longer exists; creating a new one")

    env = client.beta.environments.create(
        name="talent-sourcer-env",
        config={"type": "cloud", "networking": {"type": "unrestricted"}},
    )
    set_key(str(ENV_PATH), "ENVIRONMENT_ID", env.id)
    print(f"  created environment: {env.id}")
    return env.id


def _get_or_create_skill(client: anthropic.Anthropic) -> str:
    skill_bytes = SKILL_PATH.read_bytes()
    skill_id = os.getenv("SKILL_ID")

    # The skills API expects each file's filename to include a top-level
    # directory prefix. The API extracts that directory name as the skill's
    # internal name and requires SKILL.md to live at its root.
    skill_files = [("talent-sourcer/SKILL.md", skill_bytes, "text/markdown")]

    if skill_id:
        try:
            client.beta.skills.retrieve(skill_id)
            version = client.beta.skills.versions.create(
                skill_id,
                files=skill_files,
            )
            print(f"  updated skill {skill_id} -> version {getattr(version, 'version', '?')}")
            return skill_id
        except anthropic.NotFoundError:
            print(f"  stored SKILL_ID {skill_id} no longer exists; creating a new one")

    skill = client.beta.skills.create(
        display_title="Talent Sourcer",
        files=skill_files,
    )
    set_key(str(ENV_PATH), "SKILL_ID", skill.id)
    print(f"  created skill: {skill.id}")
    return skill.id


def _get_or_create_agent(client: anthropic.Anthropic, skill_id: str) -> str:
    agent_id = os.getenv("AGENT_ID")
    if agent_id:
        try:
            client.beta.agents.retrieve(agent_id)
            print(f"  using existing agent: {agent_id}")
            print("    (delete AGENT_ID from .env and re-run to recreate with new system prompt/tools)")
            return agent_id
        except anthropic.NotFoundError:
            print(f"  stored AGENT_ID {agent_id} no longer exists; creating a new one")

    agent = client.beta.agents.create(
        name="Talent Sourcer",
        model=MODEL,
        system=SYSTEM_PROMPT,
        tools=[{"type": "agent_toolset_20260401"}, *CUSTOM_TOOLS],
        skills=[{"type": "custom", "skill_id": skill_id}],
    )
    set_key(str(ENV_PATH), "AGENT_ID", agent.id)
    print(f"  created agent: {agent.id}")
    return agent.id


def main() -> int:
    if not SKILL_PATH.exists():
        print(f"error: missing skill file at {SKILL_PATH}", file=sys.stderr)
        return 1

    _ensure_env_file()
    load_dotenv(ENV_PATH)

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("error: ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill it in.", file=sys.stderr)
        return 1

    client = anthropic.Anthropic()

    print("Setting up Talent Sourcer agent...")
    print()
    print("[1/3] Environment")
    env_id = _get_or_create_environment(client)
    print()
    print("[2/3] Skill")
    skill_id = _get_or_create_skill(client)
    print()
    print("[3/3] Agent")
    agent_id = _get_or_create_agent(client, skill_id)
    print()
    print("Setup complete. Saved to .env:")
    print(f"  ENVIRONMENT_ID={env_id}")
    print(f"  SKILL_ID={skill_id}")
    print(f"  AGENT_ID={agent_id}")
    print()
    print("Next: python sourcer.py --file role.txt")
    print("  (or pass a non-LinkedIn job URL: careers page / Indeed / Glassdoor)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
