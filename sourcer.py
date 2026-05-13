"""Run one talent sourcing job against the Talent Sourcer agent.

Usage:
    python sourcer.py --file role.txt
    python sourcer.py "<public_job_url>"   # careers page, Indeed, Glassdoor

LinkedIn URLs are accepted but the agent will skip the fetch and go straight
to a web_search for a public mirror.

Reads AGENT_ID / ENVIRONMENT_ID from .env (populated by setup_agent.py).
Creates a session, streams agent events, executes host-side custom tools
(github_search_users + add_candidate), writes candidates.csv INCREMENTALLY
(one row per add_candidate call — survives crashes), writes a full
provenance audit log, and reconnects automatically if the SSE stream drops.
"""

from __future__ import annotations

import argparse
import csv
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

TARGET_COUNT = 10

CSV_HEADER = [
    "rank", "match_score", "name", "current_title", "current_company",
    "location", "email", "profile_url", "source", "source_query", "source_url",
    "reason",
]

# Sonnet 4.6 pricing per 1M tokens (cache write = 1.25x input, cache read = 0.1x input)
PRICE_IN = 3.00
PRICE_OUT = 15.00


# ---------------------------------------------------------------------------
# Incremental CSV — write each candidate as it's added, dedupe, re-rank at end
# ---------------------------------------------------------------------------

class CandidatesCSV:
    """Append-only CSV that flushes after every row.

    A header is written at construction time (overwriting any prior file).
    `append(candidate)` flushes immediately so a mid-run crash leaves a valid
    partial CSV on disk. `finalize()` re-sorts by match_score and assigns the
    final rank column.
    """

    def __init__(self, path: pathlib.Path, target: int) -> None:
        self.path = path
        self.target = target
        self.count = 0
        self._seen: set[tuple[str, str]] = set()
        with self.path.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(CSV_HEADER)

    @staticmethod
    def _dedup_key(candidate: dict[str, Any]) -> tuple[str, str]:
        name = (candidate.get("name") or "").strip().lower()
        url = (candidate.get("profile_url") or "").strip().lower()
        return (name, url)

    def append(self, candidate: dict[str, Any]) -> dict[str, Any]:
        """Append one candidate. Returns a result dict for the agent."""
        if self.count >= self.target:
            return {
                "added": False,
                "complete": True,
                "count": self.count,
                "remaining": 0,
                "message": f"Target of {self.target} already reached. STOP — do not call add_candidate again.",
            }

        key = self._dedup_key(candidate)
        if not key[0]:
            return {"added": False, "error": "name is required"}
        if key in self._seen:
            return {
                "added": False,
                "duplicate": True,
                "count": self.count,
                "remaining": self.target - self.count,
                "message": "Already added (matched on name + profile_url). Continue with the next candidate.",
            }

        try:
            score = int(candidate.get("match_score") or 0)
        except (TypeError, ValueError):
            score = 0

        row = [
            self.count + 1,  # provisional rank — fixed in finalize()
            score,
            candidate.get("name", ""),
            candidate.get("current_title", ""),
            candidate.get("current_company", ""),
            candidate.get("location", ""),
            candidate.get("email", ""),
            candidate.get("profile_url", ""),
            candidate.get("source", ""),
            candidate.get("source_query", ""),
            candidate.get("source_url", ""),
            candidate.get("reason", ""),
        ]
        with self.path.open("a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(row)
        self._seen.add(key)
        self.count += 1
        remaining = self.target - self.count
        complete = remaining <= 0
        msg = f"Candidate {self.count} of {self.target} added."
        if complete:
            msg += " STOP — target reached, do not call add_candidate again."
        return {
            "added": True,
            "rank": self.count,
            "count": self.count,
            "remaining": remaining,
            "complete": complete,
            "message": msg,
        }

    def finalize(self) -> None:
        """Re-sort by match_score desc, fix the rank column."""
        if self.count == 0:
            return
        with self.path.open(encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
            rows = list(reader)

        def score_of(r: list[str]) -> int:
            try:
                return int(r[1])
            except (ValueError, IndexError):
                return 0

        rows.sort(key=score_of, reverse=True)
        for i, row in enumerate(rows, 1):
            row[0] = str(i)
        with self.path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)


# ---------------------------------------------------------------------------
# Custom tools — execute host-side, secrets stay outside the container
# ---------------------------------------------------------------------------

def github_search_users(query: str, per_page: int = 20) -> dict[str, Any]:
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


def dispatch_custom_tool(
    name: str,
    tool_input: dict[str, Any],
    csv_writer: CandidatesCSV,
) -> tuple[dict[str, Any], bool]:
    """Run a custom tool and return (result_dict, is_error)."""
    if name == "github_search_users":
        result = github_search_users(
            query=tool_input.get("query", ""),
            per_page=tool_input.get("per_page", 20),
        )
        return result, "error" in result
    if name == "add_candidate":
        result = csv_writer.append(tool_input)
        return result, bool(result.get("error"))
    return {"error": f"unknown custom tool: {name}"}, True


# ---------------------------------------------------------------------------
# Provenance — live stdout + JSONL audit file
# ---------------------------------------------------------------------------

class Provenance:
    def __init__(self, path: pathlib.Path) -> None:
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

    def tool_result(
        self,
        kind: str,
        name: str | None,
        content: Any,
        tool_use_id: str | None,
        is_error: bool = False,
    ) -> None:
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
    # add_candidate gets a special-cased short summary
    if "name" in tool_input and "match_score" in tool_input:
        name = tool_input.get("name", "?")
        score = tool_input.get("match_score", "?")
        src = tool_input.get("source", "?")
        return f'name="{name}" score={score} source={src}'
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
        if content.get("added"):
            return f"added rank={content.get('rank')} count={content.get('count')}/{TARGET_COUNT}"
        if content.get("duplicate"):
            return f"duplicate (count={content.get('count')}/{TARGET_COUNT})"
        if content.get("complete"):
            return f"target reached ({content.get('count')}/{TARGET_COUNT})"
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

def _read_text_robust(path: pathlib.Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_bytes().decode("utf-8", errors="replace")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Talent Sourcer")
    parser.add_argument(
        "url", nargs="?",
        help="Public job posting URL (careers page, Indeed, Glassdoor, etc.)",
    )
    parser.add_argument(
        "--file",
        help="Read JD text from a file instead of fetching a URL (UTF-8 preferred)",
    )
    args = parser.parse_args()
    if not (args.url or args.file):
        parser.error("provide a URL positional argument or --file")
    if args.url and args.file:
        parser.error("provide a URL or --file, not both")
    return args


def _kickoff_message(args: argparse.Namespace) -> str:
    common_tail = (
        f"Target: {TARGET_COUNT} candidates total. Call `add_candidate` ONCE "
        "per candidate as you confirm them — the tool persists each one to "
        "disk immediately. STOP when it reports `complete: true`.\n\n"
        "Sources: GitHub API (`github_search_users`) + Google web search of "
        "stackoverflow.com, news.ycombinator.com, dev.to, personal sites. "
        "**Do NOT search LinkedIn** — it's blocked and yields no useful data."
    )
    if args.file:
        jd = _read_text_robust(pathlib.Path(args.file))
        return (
            "Source candidates for this job description per the talent-sourcer "
            "skill.\n\n"
            f"{common_tail}\n\n"
            f"--- JOB DESCRIPTION ---\n{jd}"
        )
    return (
        "Source candidates for this job posting per the talent-sourcer skill.\n\n"
        f"{common_tail}\n\n"
        f"URL: {args.url}\n\n"
        "If the URL is on linkedin.com, **do NOT try web_fetch** — go straight "
        "to a web_search for a public mirror. Per the skill, if after 2 "
        "web_fetch attempts plus 1 fallback web_search you still don't have "
        "the JD, STOP and ask me to re-run with `--file role.txt`."
    )


# ---------------------------------------------------------------------------
# Streaming with reconnect-on-disconnect
# ---------------------------------------------------------------------------

# Exceptions worth retrying. anthropic.APIConnectionError wraps the underlying
# httpx errors when the SSE stream drops; APITimeoutError wraps long-request
# timeouts that the SDK raises on extended idleness.
_RECONNECT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.ConnectError,
    httpx.ReadTimeout,
)


def _stream_with_reconnect(
    client: anthropic.Anthropic,
    session_id: str,
    handle_event,
    prov: Provenance,
    max_retries: int = 3,
) -> None:
    """Stream events; on connection drop, replay history via events.list and
    resume, deduping by event.id."""
    seen_event_ids: set[str] = set()
    responded_tool_use_ids: set[str] = set()

    for attempt in range(max_retries + 1):
        try:
            if attempt > 0:
                prov.status("reconnecting", detail={"attempt": attempt})
                # Replay history so we catch up on anything emitted during the gap
                for event in client.beta.sessions.events.list(session_id):
                    eid = getattr(event, "id", None)
                    if eid and eid in seen_event_ids:
                        continue
                    terminal = handle_event(event, seen_event_ids, responded_tool_use_ids)
                    if terminal:
                        return

            with client.beta.sessions.events.stream(session_id) as stream:
                for event in stream:
                    eid = getattr(event, "id", None)
                    if eid and eid in seen_event_ids:
                        continue
                    terminal = handle_event(event, seen_event_ids, responded_tool_use_ids)
                    if terminal:
                        return
            return  # stream ended cleanly
        except _RECONNECT_EXCEPTIONS as exc:
            if attempt >= max_retries:
                raise
            backoff = 2.0 * (2 ** attempt)
            prov.error(
                f"stream dropped ({type(exc).__name__}); retry {attempt + 1}/{max_retries} in {backoff}s",
                detail=repr(exc),
            )
            print(
                f"\n[reconnect] stream dropped ({type(exc).__name__}); "
                f"retrying in {backoff:.0f}s (attempt {attempt + 1}/{max_retries})...",
                flush=True,
            )
            time.sleep(backoff)


# ---------------------------------------------------------------------------
# Session orchestration
# ---------------------------------------------------------------------------

def _wait_for_settle(client: anthropic.Anthropic, session_id: str) -> None:
    for _ in range(10):
        try:
            s = client.beta.sessions.retrieve(session_id)
            if s.status != "running":
                return
        except _RECONNECT_EXCEPTIONS:
            return
        time.sleep(0.5)


def _print_cost(client: anthropic.Anthropic, session_id: str, prov: Provenance) -> None:
    try:
        s = client.beta.sessions.retrieve(session_id)
    except _RECONNECT_EXCEPTIONS:
        return
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
    candidates_csv = CandidatesCSV(OUTPUT_CSV, target=TARGET_COUNT)

    session = client.beta.sessions.create(
        agent=agent_id,
        environment_id=env_id,
        title="Talent sourcing run",
    )
    print(f"Session: {session.id}")
    print(f"Target:  {TARGET_COUNT} candidates -> {OUTPUT_CSV}")
    print(f"Provenance log: {PROVENANCE_PATH}\n")
    prov.status("session_created", detail={"session_id": session.id, "target": TARGET_COUNT})

    # Define the event handler — closure over client / prov / candidates_csv.
    def handle_event(event, seen_event_ids: set[str], responded_tool_use_ids: set[str]) -> bool:
        """Return True if the session is terminal and we should stop streaming."""
        et = event.type
        eid = getattr(event, "id", None)

        if et == "agent.message":
            if eid:
                seen_event_ids.add(eid)
            blocks = _content_to_block_list(getattr(event, "content", None))
            for b in blocks:
                if b.get("type") == "text":
                    text = b.get("text", "")
                    if text.strip():
                        print(text)
                        prov.agent_text(text)
            return False

        if et == "agent.tool_use":
            if eid:
                seen_event_ids.add(eid)
            prov.tool_use("builtin", event.name, getattr(event, "input", None), eid)
            return False

        if et == "agent.tool_result":
            if eid:
                seen_event_ids.add(eid)
            content = _content_to_block_list(getattr(event, "content", None))
            prov.tool_result(
                "builtin", None, content,
                getattr(event, "tool_use_id", None),
                is_error=getattr(event, "is_error", False) or False,
            )
            return False

        if et == "agent.custom_tool_use":
            # Don't mark the event seen until we successfully respond — if
            # the network drops between dispatch and send, the retry path
            # will see this event again and re-handle it.
            if eid and eid in responded_tool_use_ids:
                # Already responded on a previous attempt; safe to mark seen.
                if eid:
                    seen_event_ids.add(eid)
                return False
            tool_input = getattr(event, "input", None) or {}
            prov.tool_use("custom", event.name, tool_input, eid)
            result, is_error = dispatch_custom_tool(event.name, tool_input, candidates_csv)
            prov.tool_result("custom", event.name, result, eid, is_error=is_error)
            client.beta.sessions.events.send(
                session.id,
                events=[{
                    "type": "user.custom_tool_result",
                    "custom_tool_use_id": eid,
                    "content": [{"type": "text", "text": json.dumps(result, default=str)}],
                    "is_error": is_error,
                }],
            )
            if eid:
                responded_tool_use_ids.add(eid)
                seen_event_ids.add(eid)
            return False

        if et == "session.error":
            if eid:
                seen_event_ids.add(eid)
            err = getattr(event, "error", None)
            msg = getattr(err, "message", str(err)) if err else "(no detail)"
            prov.error(msg, detail=err)
            return False

        if et == "session.status_terminated":
            if eid:
                seen_event_ids.add(eid)
            prov.status("terminated")
            print("\nSession terminated.")
            return True

        if et == "session.status_idle":
            if eid:
                seen_event_ids.add(eid)
            stop = getattr(event, "stop_reason", None)
            stop_type = getattr(stop, "type", None) if stop else None
            if stop_type == "requires_action":
                return False
            prov.status("idle", detail={"stop_reason": stop_type})
            print("\nAgent finished.")
            return True

        # Unknown event type — log and continue
        if eid:
            seen_event_ids.add(eid)
        return False

    kickoff = _kickoff_message(args)
    crashed = False
    try:
        # Stream-first: open the stream before sending so we don't miss events.
        with client.beta.sessions.events.stream(session.id) as stream:
            client.beta.sessions.events.send(
                session.id,
                events=[{
                    "type": "user.message",
                    "content": [{"type": "text", "text": kickoff}],
                }],
            )
            seen_event_ids: set[str] = set()
            responded_tool_use_ids: set[str] = set()
            terminal = False
            try:
                for event in stream:
                    eid = getattr(event, "id", None)
                    if eid and eid in seen_event_ids:
                        continue
                    if handle_event(event, seen_event_ids, responded_tool_use_ids):
                        terminal = True
                        break
            except _RECONNECT_EXCEPTIONS as exc:
                prov.error(f"stream dropped ({type(exc).__name__}); reconnecting", detail=repr(exc))
                print(
                    f"\n[reconnect] stream dropped ({type(exc).__name__}) — "
                    "reconnecting and replaying history...",
                    flush=True,
                )
                # Fall through to the reconnect-with-replay loop below
                terminal = False

        if not terminal:
            _stream_with_reconnect(client, session.id, handle_event, prov, max_retries=3)
    except KeyboardInterrupt:
        print("\nInterrupted — saving partial progress and exiting.", file=sys.stderr)
        prov.error("KeyboardInterrupt during streaming")
        crashed = True
    except Exception as exc:
        crashed = True
        err_text = _tb.format_exc()
        prov.error(f"streaming loop raised: {exc!r}", detail=err_text)
        print(f"\n[ERROR] streaming loop crashed: {exc!r}", file=sys.stderr)
        print(err_text, file=sys.stderr)

    # Always finalize the CSV, print cost, and close provenance.
    try:
        _wait_for_settle(client, session.id)
        candidates_csv.finalize()
        print(
            f"\nWrote {candidates_csv.count}/{TARGET_COUNT} candidates to "
            f"{OUTPUT_CSV}"
            + (" (partial — run was interrupted)" if crashed else "")
        )
        _print_cost(client, session.id, prov)
    except Exception as exc:
        prov.error(f"post-stream cleanup failed: {exc!r}", detail=_tb.format_exc())
        print(f"\n[ERROR] cleanup failed: {exc!r}", file=sys.stderr)
    finally:
        prov.close()
        print(f"\nFull tool-call audit trail: {PROVENANCE_PATH}")

    return 1 if crashed and candidates_csv.count == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
