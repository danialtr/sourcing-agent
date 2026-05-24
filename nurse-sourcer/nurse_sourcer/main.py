from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from nurse_sourcer.export import default_export_path, write_csv
from nurse_sourcer.models import JobBrief
from nurse_sourcer.orchestrator import load_brief, run_pipeline
from nurse_sourcer.storage import Store

DEFAULT_BRIEF = Path("config/job_brief.yaml")
DEFAULT_SOURCES = Path("config/sources.yaml")
DEFAULT_DB = Path("data/leads.db")
DEFAULT_EXPORTS = Path("data/exports")

console = Console()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )


def _cmd_run(args: argparse.Namespace) -> int:
    _setup_logging(args.verbose)
    brief_path = Path(args.brief)
    sources_path = Path(args.sources)
    db_path = Path(args.db)

    if not brief_path.exists():
        console.print(f"[red]Brief not found: {brief_path}[/red]")
        return 2
    if not sources_path.exists():
        console.print(f"[red]Sources config not found: {sources_path}[/red]")
        return 2

    summary = asyncio.run(
        run_pipeline(
            brief_path=brief_path,
            sources_path=sources_path,
            db_path=db_path,
            max_queries_override=args.max_queries,
        )
    )

    brief = load_brief(brief_path)
    csv_path = (
        Path(args.output)
        if args.output
        else default_export_path(DEFAULT_EXPORTS)
    )
    with Store(db_path) as store:
        leads = store.list_leads(
            min_score=brief.search_settings.min_score_for_csv,
            limit=args.top,
        )
    write_csv(leads, csv_path, cap=args.top or 200)
    console.print(f"[green]Wrote {len(leads)} leads to {csv_path}[/green]")

    _print_summary(summary)
    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    _setup_logging(args.verbose)
    db_path = Path(args.db)
    if not db_path.exists():
        console.print(f"[yellow]No DB yet at {db_path}. Run `nurse-sourcer run` first.[/yellow]")
        return 1
    with Store(db_path) as store:
        stats = store.stats()
        top = store.list_leads(min_score=0, limit=10)

    table = Table(title="Database stats")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    for k, v in stats.items():
        table.add_row(k, str(v))
    console.print(table)

    if top:
        top_table = Table(title="Top 10 leads")
        top_table.add_column("Score", justify="right")
        top_table.add_column("Name")
        top_table.add_column("Contact")
        top_table.add_column("Sources")
        for lead in top:
            top_table.add_row(
                str(lead.score),
                lead.display_name or "(unknown)",
                f"{lead.primary_contact_type or '-'}:{lead.primary_contact_value or '-'}",
                ",".join(lead.sources),
            )
        console.print(top_table)
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    _setup_logging(args.verbose)
    db_path = Path(args.db)
    if not db_path.exists():
        console.print(f"[yellow]No DB at {db_path}. Run `nurse-sourcer run` first.[/yellow]")
        return 1
    output = Path(args.output) if args.output else default_export_path(DEFAULT_EXPORTS)
    with Store(db_path) as store:
        leads = store.list_leads(min_score=args.min_score, limit=args.top)
    write_csv(leads, output, cap=args.top or 200)
    console.print(f"[green]Wrote {len(leads)} leads to {output}[/green]")
    return 0


def _print_summary(summary: dict) -> None:
    table = Table(title="Run summary")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Queries built", str(summary.get("queries_built", 0)))
    table.add_row("Raw hits", str(summary.get("raw_hits", 0)))
    table.add_row("Unique leads (pre-filter)", str(summary.get("unique_leads_pre_filter", 0)))
    table.add_row("Stored leads", str(summary.get("stored_leads", 0)))
    for k, v in summary.get("store_stats", {}).items():
        table.add_row(f"db.{k}", str(v))
    console.print(table)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nurse-sourcer",
        description="Find Filipino registered nurses interested in moving to Germany.",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run the full sourcing pipeline.")
    run.add_argument("--brief", default=str(DEFAULT_BRIEF))
    run.add_argument("--sources", default=str(DEFAULT_SOURCES))
    run.add_argument("--db", default=str(DEFAULT_DB))
    run.add_argument("--output", default=None, help="CSV path (default: data/exports/leads_YYYY-MM-DD.csv).")
    run.add_argument("--max-queries", type=int, default=None, help="Cap total queries for a fast smoke test.")
    run.add_argument("--top", type=int, default=200)
    run.set_defaults(func=_cmd_run)

    stats = sub.add_parser("stats", help="Show DB counts and the top 10 leads.")
    stats.add_argument("--db", default=str(DEFAULT_DB))
    stats.set_defaults(func=_cmd_stats)

    exp = sub.add_parser("export", help="Re-export ranked leads from the DB.")
    exp.add_argument("--db", default=str(DEFAULT_DB))
    exp.add_argument("--output", default=None)
    exp.add_argument("--top", type=int, default=100)
    exp.add_argument("--min-score", type=int, default=0)
    exp.set_defaults(func=_cmd_export)
    return p


def cli(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(cli())
