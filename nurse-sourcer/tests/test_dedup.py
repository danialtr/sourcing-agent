from datetime import datetime

from nurse_sourcer.dedup import deduplicate
from nurse_sourcer.models import Contacts, RawHit, Signals


def _hit(source: str, email: str | None = None, reddit: str | None = None, **signals):
    return RawHit(
        source_name=source,
        source_url=f"https://{source}.test/post",
        text="test",
        contacts=Contacts(
            emails=[email] if email else [],
            reddit_usernames=[reddit] if reddit else [],
        ),
        signals=Signals(**signals),
        fetched_at=datetime.utcnow(),
    )


def test_two_hits_with_same_email_merge_into_one_lead():
    hits = [
        _hit("brave", email="x@y.com", is_nurse=True),
        _hit("reddit", email="x@y.com", is_filipino=True),
    ]
    leads = deduplicate(hits)
    assert len(leads) == 1
    lead = leads[0]
    assert lead.signals.is_nurse
    assert lead.signals.is_filipino
    assert "brave" in lead.sources and "reddit" in lead.sources


def test_distinct_handles_stay_separate():
    hits = [
        _hit("reddit", reddit="alice", is_nurse=True),
        _hit("reddit", reddit="bob", is_nurse=True),
    ]
    leads = deduplicate(hits)
    assert len(leads) == 2
