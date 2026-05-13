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
    "description, source candidates from public web sources and produce a "
    "ranked candidates.csv at /mnt/session/outputs/candidates.csv.\n\n"
    "Follow the talent-sourcer skill for the sourcing process, scoring, and "
    "output format.\n\n"
    "Use free public sources only: the GitHub REST API (via the "
    "`github_search_users` custom tool) and Google web search of "
    "stackoverflow.com, news.ycombinator.com, dev.to, company careers pages, "
    "and personal portfolios. **Do NOT query LinkedIn** — its pages are "
    "blocked from automated fetch and yield no useful candidate data; spend "
    "the search budget elsewhere. Never fabricate data — leave a field blank "
    "if you cannot find it from public sources.\n\n"
    "Always populate the `source_query` and `source_url` columns so the user "
    "can audit where each candidate came from."
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
    print("Next: python sourcer.py \"<linkedin_job_url>\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
