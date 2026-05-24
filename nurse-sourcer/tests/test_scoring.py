from __future__ import annotations

from datetime import datetime

from nurse_sourcer.models import (
    Contacts,
    Employer,
    JobBrief,
    Lead,
    Requirements,
    Role,
    SearchSettings,
    Signals,
    TargetSource,
)
from nurse_sourcer.scoring import passes_minimum_signal, score_lead


def _brief() -> JobBrief:
    return JobBrief(
        job_id="test-1",
        employer=Employer(
            name="Uniklinikum Leipzig",
            type="hospital",
            location_city="Leipzig",
            country="Germany",
        ),
        role=Role(
            german_title="Pflegefachkraft",
            english_title="RN",
            specialty="ICU",
            specialty_aliases=["intensive care"],
            trainee_or_qualified="qualified",
            positions_open=5,
        ),
        requirements=Requirements(
            min_years_experience=2,
            nursing_qualification="BSN",
            german_level_at_application="B1",
            german_level_required_within_12_months="B2",
        ),
        target_source=TargetSource(
            country="Philippines",
            country_aliases=["Filipino", "Pinoy"],
        ),
        search_settings=SearchSettings(),
    )


def _lead(**signals: bool | int | None) -> Lead:
    s = Signals(**signals)
    return Lead(
        identity_key="email:foo@example.com",
        display_name="Test User",
        signals=s,
        contacts=Contacts(emails=["foo@example.com"]),
        primary_contact_type="email",
        primary_contact_value="foo@example.com",
        first_seen_at=datetime.utcnow(),
        last_seen_at=datetime.utcnow(),
        sources=["brave"],
    )


def test_minimum_signal_requires_nurse():
    lead = _lead(is_nurse=False, is_filipino=True, mentions_germany=True)
    assert not passes_minimum_signal(lead)


def test_minimum_signal_passes_with_nurse_and_filipino():
    lead = _lead(is_nurse=True, is_filipino=True)
    assert passes_minimum_signal(lead)


def test_high_signal_lead_scores_high():
    lead = _lead(
        is_nurse=True,
        is_filipino=True,
        mentions_germany=True,
        mentions_language=True,
        mentions_icu=True,
        mentions_program=True,
        years_experience=5,
    )
    score = score_lead(lead, _brief())
    # 20 + 15 + 15 + 10 (icu) + 10 (lang) + 10 (program) + 5 (years) + 10 (contact) + 5 (recent) = 100
    assert score == 100


def test_basic_signal_lead_scores_lower():
    lead = _lead(is_nurse=True, is_filipino=True, mentions_germany=True)
    score = score_lead(lead, _brief())
    # 20 + 15 + 15 + 10 (contact) + 5 (recent) = 65
    assert score == 65


def test_no_contact_path_drops_ten():
    lead = _lead(is_nurse=True, is_filipino=True, mentions_germany=True)
    lead.contacts = Contacts()
    lead.primary_contact_type = None
    lead.primary_contact_value = None
    score = score_lead(lead, _brief())
    assert score == 55
