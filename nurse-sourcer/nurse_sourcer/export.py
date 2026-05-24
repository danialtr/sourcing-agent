from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from nurse_sourcer.models import Lead

CSV_FIELDS = [
    "score",
    "name",
    "primary_contact_type",
    "primary_contact_value",
    "is_nurse",
    "is_filipino",
    "mentions_germany",
    "mentions_language",
    "mentions_icu",
    "mentions_program",
    "years_experience",
    "source_count",
    "sources",
    "first_seen",
    "last_seen",
    "profile_links",
]


def _profile_links(lead: Lead) -> str:
    parts: list[str] = []
    for h in lead.contacts.linkedin_handles:
        parts.append(f"linkedin.com/in/{h}")
    for h in lead.contacts.youtube_channels:
        parts.append(f"youtube.com/@{h}")
    for h in lead.contacts.facebook_handles:
        parts.append(f"facebook.com/{h}")
    for h in lead.contacts.tiktok_handles:
        parts.append(f"tiktok.com/@{h}")
    for h in lead.contacts.reddit_usernames:
        parts.append(f"reddit.com/u/{h}")
    return "|".join(parts)


def write_csv(leads: list[Lead], output_path: Path, cap: int = 200) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    leads_sorted = sorted(leads, key=lambda lead_: lead_.score, reverse=True)[:cap]

    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for lead in leads_sorted:
            writer.writerow(
                {
                    "score": lead.score,
                    "name": lead.display_name or "",
                    "primary_contact_type": lead.primary_contact_type or "",
                    "primary_contact_value": lead.primary_contact_value or "",
                    "is_nurse": lead.signals.is_nurse,
                    "is_filipino": lead.signals.is_filipino,
                    "mentions_germany": lead.signals.mentions_germany,
                    "mentions_language": lead.signals.mentions_language,
                    "mentions_icu": lead.signals.mentions_icu,
                    "mentions_program": lead.signals.mentions_program,
                    "years_experience": lead.signals.years_experience or "",
                    "source_count": len(lead.sources),
                    "sources": ",".join(lead.sources),
                    "first_seen": lead.first_seen_at.date().isoformat(),
                    "last_seen": lead.last_seen_at.date().isoformat(),
                    "profile_links": _profile_links(lead),
                }
            )
    return output_path


def default_export_path(exports_dir: Path) -> Path:
    return exports_dir / f"leads_{date.today().isoformat()}.csv"
