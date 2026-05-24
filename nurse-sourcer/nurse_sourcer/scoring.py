from __future__ import annotations

from nurse_sourcer.models import JobBrief, Lead


def passes_minimum_signal(lead: Lead) -> bool:
    """Gate so we don't store pure noise."""
    return lead.signals.is_nurse and (
        lead.signals.is_filipino or lead.signals.mentions_germany
    )


def score_lead(lead: Lead, brief: JobBrief) -> int:
    score = 0

    if lead.signals.is_nurse:
        score += 20
    if lead.signals.is_filipino:
        score += 15
    if lead.signals.mentions_germany:
        score += 15

    if lead.signals.mentions_icu and brief.role.specialty.upper() == "ICU":
        score += 10

    if lead.signals.mentions_language:
        score += 10

    if lead.signals.mentions_program:
        score += 10

    if (lead.signals.years_experience or 0) >= brief.requirements.min_years_experience:
        if lead.signals.years_experience is not None:
            score += 5

    if lead.has_contact_path():
        score += 10

    if lead.was_active_recently():
        score += 5

    return min(score, 100)
