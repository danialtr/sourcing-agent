from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from nurse_sourcer.dedup import merge_into
from nurse_sourcer.models import Contacts, Lead, Signals

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY,
    identity_key TEXT UNIQUE NOT NULL,
    display_name TEXT,
    score INTEGER DEFAULT 0,
    is_nurse INTEGER DEFAULT 0,
    is_filipino INTEGER DEFAULT 0,
    mentions_germany INTEGER DEFAULT 0,
    mentions_language INTEGER DEFAULT 0,
    mentions_icu INTEGER DEFAULT 0,
    mentions_program INTEGER DEFAULT 0,
    years_experience INTEGER,
    primary_contact_type TEXT,
    primary_contact_value TEXT,
    first_seen_at TEXT,
    last_seen_at TEXT,
    raw_data_json TEXT
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    lead_id INTEGER REFERENCES leads(id),
    source_name TEXT,
    source_url TEXT,
    raw_text TEXT,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS queries_log (
    id INTEGER PRIMARY KEY,
    query_text TEXT,
    source TEXT,
    results_count INTEGER,
    quota_used INTEGER,
    ran_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_leads_score ON leads(score DESC);
CREATE INDEX IF NOT EXISTS idx_sources_lead ON sources(lead_id);
"""


class Store:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *a: object) -> None:
        self.close()

    # --- Lead persistence -----------------------------------------------

    def get_lead_by_key(self, identity_key: str) -> Lead | None:
        row = self.conn.execute(
            "SELECT * FROM leads WHERE identity_key = ?", (identity_key,)
        ).fetchone()
        if not row:
            return None
        return _row_to_lead(row)

    def upsert_lead(self, lead: Lead) -> int:
        existing = self.get_lead_by_key(lead.identity_key)
        if existing:
            merged = _merge_leads(existing, lead)
        else:
            merged = lead

        payload = (
            merged.identity_key,
            merged.display_name,
            merged.score,
            int(merged.signals.is_nurse),
            int(merged.signals.is_filipino),
            int(merged.signals.mentions_germany),
            int(merged.signals.mentions_language),
            int(merged.signals.mentions_icu),
            int(merged.signals.mentions_program),
            merged.signals.years_experience,
            merged.primary_contact_type,
            merged.primary_contact_value,
            merged.first_seen_at.isoformat(),
            merged.last_seen_at.isoformat(),
            json.dumps(
                {
                    "contacts": merged.contacts.model_dump(),
                    "sources": merged.sources,
                    "source_urls": merged.source_urls,
                }
            ),
        )

        if existing:
            self.conn.execute(
                """
                UPDATE leads SET
                    display_name = ?,
                    score = ?,
                    is_nurse = ?,
                    is_filipino = ?,
                    mentions_germany = ?,
                    mentions_language = ?,
                    mentions_icu = ?,
                    mentions_program = ?,
                    years_experience = ?,
                    primary_contact_type = ?,
                    primary_contact_value = ?,
                    first_seen_at = ?,
                    last_seen_at = ?,
                    raw_data_json = ?
                WHERE identity_key = ?
                """,
                payload[1:] + (merged.identity_key,),
            )
            row = self.conn.execute(
                "SELECT id FROM leads WHERE identity_key = ?",
                (merged.identity_key,),
            ).fetchone()
            lead_id = int(row["id"])
        else:
            cur = self.conn.execute(
                """
                INSERT INTO leads (
                    identity_key, display_name, score,
                    is_nurse, is_filipino, mentions_germany,
                    mentions_language, mentions_icu, mentions_program,
                    years_experience, primary_contact_type, primary_contact_value,
                    first_seen_at, last_seen_at, raw_data_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payload,
            )
            lead_id = int(cur.lastrowid)
        self.conn.commit()
        return lead_id

    def add_source(
        self,
        lead_id: int,
        source_name: str,
        source_url: str,
        raw_text: str,
        fetched_at: datetime,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO sources (lead_id, source_name, source_url, raw_text, fetched_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (lead_id, source_name, source_url, raw_text, fetched_at.isoformat()),
        )
        self.conn.commit()

    def log_query(
        self,
        query_text: str,
        source: str,
        results_count: int,
        quota_used: int = 0,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO queries_log (query_text, source, results_count, quota_used, ran_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                query_text,
                source,
                results_count,
                quota_used,
                datetime.utcnow().isoformat(),
            ),
        )
        self.conn.commit()

    def list_leads(self, min_score: int = 0, limit: int | None = None) -> list[Lead]:
        sql = "SELECT * FROM leads WHERE score >= ? ORDER BY score DESC"
        params: list = [min_score]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [_row_to_lead(r) for r in self.conn.execute(sql, params).fetchall()]

    def stats(self) -> dict[str, int]:
        c = self.conn.execute("SELECT COUNT(*) AS n FROM leads").fetchone()
        total = int(c["n"])
        with_contact = int(
            self.conn.execute(
                "SELECT COUNT(*) AS n FROM leads WHERE primary_contact_value IS NOT NULL"
            ).fetchone()["n"]
        )
        gte70 = int(
            self.conn.execute(
                "SELECT COUNT(*) AS n FROM leads WHERE score >= 70"
            ).fetchone()["n"]
        )
        gte85 = int(
            self.conn.execute(
                "SELECT COUNT(*) AS n FROM leads WHERE score >= 85"
            ).fetchone()["n"]
        )
        sources_total = int(
            self.conn.execute("SELECT COUNT(*) AS n FROM sources").fetchone()["n"]
        )
        queries_total = int(
            self.conn.execute("SELECT COUNT(*) AS n FROM queries_log").fetchone()["n"]
        )
        return {
            "total_leads": total,
            "with_contact": with_contact,
            "score_gte_70": gte70,
            "score_gte_85": gte85,
            "raw_sources": sources_total,
            "queries_run": queries_total,
        }


def _row_to_lead(row: sqlite3.Row) -> Lead:
    raw = json.loads(row["raw_data_json"] or "{}")
    contacts = Contacts(**raw.get("contacts", {}))
    signals = Signals(
        is_nurse=bool(row["is_nurse"]),
        is_filipino=bool(row["is_filipino"]),
        mentions_germany=bool(row["mentions_germany"]),
        mentions_language=bool(row["mentions_language"]),
        mentions_icu=bool(row["mentions_icu"]),
        mentions_program=bool(row["mentions_program"]),
        years_experience=row["years_experience"],
    )
    return Lead(
        identity_key=row["identity_key"],
        display_name=row["display_name"],
        score=row["score"] or 0,
        signals=signals,
        contacts=contacts,
        primary_contact_type=row["primary_contact_type"],
        primary_contact_value=row["primary_contact_value"],
        first_seen_at=datetime.fromisoformat(row["first_seen_at"]),
        last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
        sources=raw.get("sources", []),
        source_urls=raw.get("source_urls", []),
    )


def _merge_leads(existing: Lead, new: Lead) -> Lead:
    """Merge a new in-memory Lead into one already on disk."""
    # Reuse the RawHit-flavored merge by reconstructing a hit-like payload.
    from nurse_sourcer.models import RawHit

    pseudo_hit = RawHit(
        source_name=new.sources[0] if new.sources else "unknown",
        source_url=new.source_urls[0] if new.source_urls else "",
        text="",
        display_name=new.display_name,
        contacts=new.contacts,
        signals=new.signals,
        fetched_at=new.last_seen_at,
    )
    merged = merge_into(existing, pseudo_hit)
    # Preserve the better display name + the wider union of sources.
    if not merged.display_name and new.display_name:
        merged = merged.model_copy(update={"display_name": new.display_name})
    if new.score and new.score > existing.score:
        merged = merged.model_copy(update={"score": new.score})
    else:
        merged = merged.model_copy(update={"score": existing.score})
    return merged
