"""Run one talent sourcing job against the Talent Sourcer agent.

Usage:
    python sourcer.py "<linkedin_job_url>"
    python sourcer.py --file role.txt

Reads AGENT_ID / ENVIRONMENT_ID from .env (populated by setup_agent.py).
Creates a session, streams agent events to stdout, handles host-side custom
tools (currently: github_search_users), writes a full provenance audit log,
then downloads candidates.csv from the session's output directory.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import traceback as _tb
from typing import Any

import anthropic
import httpx
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).parent
ENV_PATH = ROOT / ".env"
OUTPUT_CSV = ROOT / "candidates.csv"
PROVENANCE_PATH = ROOT / "provenance.jsonl"

# Sonnet 4.6 pricing per 1M tokens (cache write = 1.25x input, cache read = 0.1x input)
PRICE_IN = 3.00
PRICE_OUT = 15.00


# ---------------------------------------------------------------------------
# Custom tools (host-side, free, secrets stay outside the container)
# ---------------------------------------------------------------------------

def github_search_users(query: str, per_page: int = 20) -> dict[str, Any]:
    """Search GitHub users, then fetch each user's full public profile.

    Uses GITHUB_PAT from .env if set (5000 req/hour). Falls back to unauth
    (60 req/hour). Returns a JSON-serializable dict.
    """
    pat = os.getenv("GITHUB_PAT")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "talent-sourcer"}
    if pat:
        headers["Authorization"] = f"Bearer {pat}"

    per_page = max(1, min(int(per_page or 20), 30))

    try:
        with httpx.Client(timeout=30.0, headers=headers) as client:
            search = client.get(
                "https://api.github.com/search/users",
                params={"q": query, "per_page": per_page},
            )
            if search.status_code == 401:
                return {"error": "GitHub auth failed — check GITHUB_PAT in .env"}
            if search.status_code == 403:
                return {"error": f"GitHub rate-limited or forbidden: {search.text[:200]}"}
            if search.status_code >= 400:
                return {"error": f"GitHub search returned {search.status_code}: {search.text[:200]}"}

            payload = search.json()
            items = payload.get("items", []) or []
            users: list[dict[str, Any]] = []
            for item in items:
                login = item.get("login")
                if not login:
                    continue
                try:
                    profile_resp = client.get(f"https://api.github.com/users/{login}")
                    if profile_resp.status_code >= 400:
                        users.append({"login": login, "html_url": item.get("html_url")})
                        continue
                    p = profile_resp.json()
                    users.append({
                        "login": p.get("login"),
                        "name": p.get("name"),
                        "bio": p.get("bio"),
                        "location": p.get("location"),
                        "company": p.get("company"),
                        "blog": p.get("blog"),
                        "email": p.get("email"),
                        "html_url": p.get("html_url"),
                        "followers": p.get("followers"),
                        "public_repos": p.get("public_repos"),
                    })
                except Exception as exc:
                    users.append({"login": login, "fetch_error": str(exc)})
            return {
                "query": query,
                "total_count": payload.get("total_count", 0),
                "returned": len(users),
                "users": users,
            }
    except httpx.HTTPError as exc:
        return {"error": f"HTTP error: {exc}"}


def dispatch_custom_tool(name: str, tool_input: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Run a custom tool and return (result_dict, is_error)."""
    if name == "github_search_users":
        result = github_search_users(
            query=tool_input.get("query", ""),
            per_page=tool_input.get("per_page", 20),
        )
        return result, "error" in result
    return {"error": f"unknown custom tool: {name}"}, True


# ---------------------------------------------------------------------------
# Provenance logging — live stdout + JSONL audit file
# ---------------------------------------------------------------------------

class Provenance:
    """Writes one JSON object per line to provenance.jsonl and prints a
    compact tool-call summary to stdout so the user can watch live."""

    def __init__(self, path: pathlib.Path):
        self.path = path
        self.fh = path.open("w")
        self.start = time.monotonic()

    def _t(self) -> float:
        return round(time.monotonic() - self.start, 2)

    def _write(self, **fields: Any) -> None:
        line = {"t": self._t(), **fields}
        self.fh.write(json.dumps(line, default=str) + "\n")
        self.fh.flush()

    def tool_use(self, kind: str, name: str, tool_input: Any, tool_use_id: str | None) -> None:
        self._write(event="tool_use", kind=kind, name=name, input=tool_input, id=tool_use_id)
        print(f"[{self._t():>5.1f}s] {name}  {_summarize_input(tool_input)}", flush=True)

    def tool_result(self, kind: str, name: str | None, content: Any, tool_use_id: str | None,
                    is_error: bool = False) -> None:
        summary = _summarize_result(content)
        self._write(
            event="tool_result", kind=kind, name=name, id=tool_use_id,
            is_error=is_error, summary=summary,
            content_preview=_preview(content, 2000),
        )
        prefix = "ERROR" if is_error else "->"
        label = name or "(result)"
        print(f"[{self._t():>5.1f}s]   {prefix} {label}: {summary}", flush=True)

    def agent_text(self, text: str) -> None:
        self._write(event="agent_text", text=text)

    def status(self, status: str, detail: Any = None) -> None:
        self._write(event="status", status=status, detail=detail)
        print(f"[{self._t():>5.1f}s] [status] {status}", flush=True)

    def error(self, message: str, detail: Any = None) -> None:
        self._write(event="error", message=message, detail=detail)
        print(f"[{self._t():>5.1f}s] [error] {message}", flush=True)

    def usage(self, usage: dict[str, Any]) -> None:
        self._write(event="usage", **usage)

    def close(self) -> None:
        self.fh.close()


def _summarize_input(tool_input: Any) -> str:
    if not isinstance(tool_input, dict):
        return _preview(tool_input, 120)
    for key in ("query", "url", "command", "path", "file_path"):
        if key in tool_input:
            return f'{key}="{_preview(tool_input[key], 120)}"'
    return _preview(tool_input, 120)


def _summarize_result(content: Any) -> str:
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return _preview(block.get("text", ""), 120)
        return f"{len(content)} block(s)"
    if isinstance(content, dict):
        if "users" in content and isinstance(content["users"], list):
            logins = [u.get("login") for u in content["users"][:5] if u.get("login")]
            tail = "..." if len(content["users"]) > 5 else ""
            return f"{content.get('returned', len(content['users']))} users ({', '.join(logins)}{tail})"
        if "error" in content:
            return f"error: {content['error']}"
        return _preview(content, 120)
    return _preview(content, 120)


def _preview(value: Any, max_chars: int) -> str:
    if isinstance(value, (dict, list)):
        s = json.dumps(value, default=str, ensure_ascii=False)
    else:
        s = str(value)
    s = s.replace("\n", " ")
    return s if len(s) <= max_chars else s[:max_chars] + "..."


def _content_to_block_list(content: Any) -> list[Any]:
    """Normalize event.content (Pydantic blocks or list of dicts) to a plain list."""
    out: list[Any] = []
    if not content:
        return out
    for b in content:
        if hasattr(b, "model_dump"):
            out.append(b.model_dump())
        elif isinstance(b, dict):
            out.append(b)
        else:
            out.append({"type": getattr(b, "type", "?"), "raw": str(b)})
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

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


def _read_text_robust(path: pathlib.Path) -> str:
    """Read a text file without crashing on Windows-default cp1252.

    Tries UTF-8 (with and without BOM) first, then cp1252, then latin-1
    (which can decode any byte sequence — last-resort, may render odd).
    """
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_bytes().decode("utf-8", errors="replace")


def _kickoff_message(args: argparse.Namespace) -> str:
    if args.file:
        jd = _read_text_robust(pathlib.Path(args.file))
        return (
            "Source candidates for this job description. Write the result to "
            "/mnt/session/outputs/candidates.csv per the talent-sourcer skill. "
            "Always populate source_query and source_url so I can see where each "
            "candidate was found.\n\n"
            f"--- JOB DESCRIPTION ---\n{jd}"
        )
    return (
        "Source candidates for this LinkedIn job posting. Write the result to "
        "/mnt/session/outputs/candidates.csv per the talent-sourcer skill. "
        "Always populate source_query and source_url so I can see where each "
        "candidate was found.\n\n"
        f"URL: {args.url}\n\n"
        "IMPORTANT: LinkedIn job URLs frequently return `url_not_allowed` from "
        "web_fetch. Per the skill, attempt at most 2 web_fetch calls plus 1 "
        "fallback web_search. If you still can't get the JD after that, STOP "
        "and ask the user to re-run with `--file role.txt` — do NOT keep "
        "retrying and do NOT write an empty CSV."
    )


# ---------------------------------------------------------------------------
# Session orchestration
# ---------------------------------------------------------------------------

def _wait_for_settle(client: anthropic.Anthropic, session_id: str) -> None:
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
            response = client.beta.files.download(f.id, betas=["managed-agents-2026-04-01"])
            response.write_to_file(OUTPUT_CSV)
            print(f"\nWrote {OUTPUT_CSV} ({f.size_bytes} bytes)")
            return True
    return False


def _print_cost(client: anthropic.Anthropic, session_id: str, prov: Provenance) -> None:
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
    prov.usage({
        "input_tokens": in_t, "output_tokens": out_t,
        "cache_read_input_tokens": cache_r, "cache_creation_input_tokens": cache_w,
        "estimated_claude_cost_usd": round(cost, 4),
    })
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
    prov = Provenance(PROVENANCE_PATH)

    session = client.beta.sessions.create(
        agent=agent_id,
        environment_id=env_id,
        title="Talent sourcing run",
    )
    print(f"Session: {session.id}")
    print(f"Provenance log: {PROVENANCE_PATH}\n")
    prov.status("session_created", {"session_id": session.id})

    kickoff = _kickoff_message(args)
    crashed = False
    try:
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
                    blocks = _content_to_block_list(getattr(event, "content", None))
                    for b in blocks:
                        if b.get("type") == "text":
                            text = b.get("text", "")
                            if text.strip():
                                print(text)
                                prov.agent_text(text)

                elif et == "agent.tool_use":
                    prov.tool_use("builtin", event.name, getattr(event, "input", None), event.id)

                elif et == "agent.tool_result":
                    content = _content_to_block_list(getattr(event, "content", None))
                    tool_use_id = getattr(event, "tool_use_id", None)
                    prov.tool_result(
                        "builtin", None, content, tool_use_id,
                        is_error=getattr(event, "is_error", False) or False,
                    )

                elif et == "agent.custom_tool_use":
                    tool_input = getattr(event, "input", None) or {}
                    prov.tool_use("custom", event.name, tool_input, event.id)
                    result, is_error = dispatch_custom_tool(event.name, tool_input)
                    prov.tool_result("custom", event.name, result, event.id, is_error=is_error)
                    client.beta.sessions.events.send(
                        session.id,
                        events=[{
                            "type": "user.custom_tool_result",
                            "custom_tool_use_id": event.id,
                            "content": [{"type": "text", "text": json.dumps(result, default=str)}],
                            "is_error": is_error,
                        }],
                    )

                elif et == "session.error":
                    err = getattr(event, "error", None)
                    msg = getattr(err, "message", str(err)) if err else "(no detail)"
                    prov.error(msg, detail=err)

                elif et == "session.status_terminated":
                    prov.status("terminated")
                    print("\nSession terminated.")
                    break

                elif et == "session.status_idle":
                    stop = getattr(event, "stop_reason", None)
                    stop_type = getattr(stop, "type", None) if stop else None
                    if stop_type == "requires_action":
                        continue
                    prov.status("idle", detail={"stop_reason": stop_type})
                    print("\nAgent finished.")
                    break
    except KeyboardInterrupt:
        print("\nInterrupted — saving partial provenance and exiting.", file=sys.stderr)
        prov.error("KeyboardInterrupt during streaming")
        crashed = True
    except Exception as exc:
        crashed = True
        err_text = _tb.format_exc()
        prov.error(f"streaming loop raised: {exc!r}", detail=err_text)
        print(f"\n[ERROR] streaming loop crashed: {exc!r}", file=sys.stderr)
        print(err_text, file=sys.stderr)

    # Always try to settle, download whatever exists, and report cost.
    try:
        _wait_for_settle(client, session.id)
        found = _download_csv(client, session.id)
        if not found:
            msg = "candidates.csv was not produced"
            if crashed:
                msg += " (sourcer crashed mid-stream — see traceback above)"
            print(f"\nWARNING: {msg}. Session ID for debugging: {session.id}")
        _print_cost(client, session.id, prov)
    except Exception as exc:
        prov.error(f"post-stream cleanup failed: {exc!r}", detail=_tb.format_exc())
        print(f"\n[ERROR] cleanup failed: {exc!r}", file=sys.stderr)
    finally:
        prov.close()
        print(f"\nFull tool-call audit trail: {PROVENANCE_PATH}")

    return 1 if crashed else 0


if __name__ == "__main__":
    sys.exit(main())
