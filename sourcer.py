"""Run one talent sourcing job against the Talent Sourcer agent.

Usage:
    python sourcer.py "<linkedin_job_url>"
    python sourcer.py --file role.txt

Reads AGENT_ID / ENVIRONMENT_ID from .env (populated by setup_agent.py).
Creates a session, streams agent events to stdout, then downloads
candidates.csv from the session's output directory.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time

import anthropic
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).parent
ENV_PATH = ROOT / ".env"
OUTPUT_CSV = ROOT / "candidates.csv"

# Sonnet 4.6 pricing per 1M tokens — used only to print a cost estimate at the
# end. Cache write is 1.25x input rate, cache read is 0.1x.
PRICE_IN = 3.00
PRICE_OUT = 15.00


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Talent Sourcer")
    parser.add_argument("url", nargs="?", help="LinkedIn job posting URL")
    parser.add_argument("--file", help="Read JD text from a file instead of fetching a URL")
    args = parser.parse_args()
    if not (args.url or args.file):
        parser.error("provide a URL positional argument or --file")
    if args.url and args.file:
        parser.error("provide a URL or --file, not both")
    return args


def _kickoff_message(args: argparse.Namespace) -> str:
    if args.file:
        jd = pathlib.Path(args.file).read_text()
        return (
            "Source candidates for this job description. Write the result to "
            "/mnt/session/outputs/candidates.csv per the talent-sourcer skill.\n\n"
            f"--- JOB DESCRIPTION ---\n{jd}"
        )
    return (
        "Source candidates for this LinkedIn job posting. Write the result to "
        "/mnt/session/outputs/candidates.csv per the talent-sourcer skill.\n\n"
        f"URL: {args.url}"
    )


def _wait_for_settle(client: anthropic.Anthropic, session_id: str) -> None:
    """Avoid the post-idle status-write race before listing session files."""
    for _ in range(10):
        s = client.beta.sessions.retrieve(session_id)
        if s.status != "running":
            return
        time.sleep(0.5)


def _download_csv(client: anthropic.Anthropic, session_id: str) -> bool:
    files_page = client.beta.files.list(
        scope_id=session_id,
        betas=["managed-agents-2026-04-01"],
    )
    for f in files_page.data:
        if f.filename.endswith("candidates.csv"):
            response = client.beta.files.download(
                f.id,
                betas=["managed-agents-2026-04-01"],
            )
            response.write_to_file(OUTPUT_CSV)
            print(f"\nWrote {OUTPUT_CSV} ({f.size_bytes} bytes)")
            return True
    return False


def _print_cost(client: anthropic.Anthropic, session_id: str) -> None:
    s = client.beta.sessions.retrieve(session_id)
    u = getattr(s, "usage", None)
    if not u:
        return
    in_t = getattr(u, "input_tokens", 0) or 0
    out_t = getattr(u, "output_tokens", 0) or 0
    cache_r = getattr(u, "cache_read_input_tokens", 0) or 0
    cache_w = getattr(u, "cache_creation_input_tokens", 0) or 0
    cost = (
        in_t * PRICE_IN
        + out_t * PRICE_OUT
        + cache_w * PRICE_IN * 1.25
        + cache_r * PRICE_IN * 0.10
    ) / 1_000_000
    print(
        f"Usage: input={in_t:,} output={out_t:,} "
        f"cache_read={cache_r:,} cache_write={cache_w:,}"
    )
    print(f"Estimated Claude cost: ${cost:.4f}")


def main() -> int:
    args = _parse_args()
    load_dotenv(ENV_PATH)

    agent_id = os.getenv("AGENT_ID")
    env_id = os.getenv("ENVIRONMENT_ID")
    if not (agent_id and env_id):
        print(
            "error: AGENT_ID or ENVIRONMENT_ID missing from .env. "
            "Run `python setup_agent.py` first.",
            file=sys.stderr,
        )
        return 1
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("error: ANTHROPIC_API_KEY is not set in .env.", file=sys.stderr)
        return 1

    client = anthropic.Anthropic()

    session = client.beta.sessions.create(
        agent=agent_id,
        environment_id=env_id,
        title="Talent sourcing run",
    )
    print(f"Session: {session.id}\n")

    kickoff = _kickoff_message(args)
    # Stream-first: subscribe before sending so we don't miss early events.
    with client.beta.sessions.events.stream(session.id) as stream:
        client.beta.sessions.events.send(
            session.id,
            events=[{
                "type": "user.message",
                "content": [{"type": "text", "text": kickoff}],
            }],
        )
        for event in stream:
            et = event.type
            if et == "agent.message":
                for block in event.content:
                    if getattr(block, "type", None) == "text":
                        print(block.text, end="", flush=True)
                print()
            elif et == "agent.tool_use":
                name = getattr(event, "name", "?")
                print(f"  [tool] {name}", flush=True)
            elif et == "session.error":
                err = getattr(event, "error", None)
                msg = getattr(err, "message", err) if err else "?"
                print(f"  [error] {msg}", flush=True)
            elif et == "session.status_terminated":
                print("\nSession terminated.")
                break
            elif et == "session.status_idle":
                stop = getattr(event, "stop_reason", None)
                stop_type = getattr(stop, "type", None) if stop else None
                if stop_type == "requires_action":
                    print("\nAgent is waiting on a tool result we can't provide. Exiting.")
                    break
                print("\nAgent finished.")
                break

    _wait_for_settle(client, session.id)

    if not _download_csv(client, session.id):
        print(
            "\nWARNING: candidates.csv was not produced. "
            f"Session ID for debugging: {session.id}"
        )
    _print_cost(client, session.id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
