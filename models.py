"""Plain dataclasses shared across the talent sourcing pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Role:
    """Parsed job description — produced by Step 2, consumed by every later step."""

    title: str
    seniority: str
    must_have_skills: list[str]
    nice_to_have_skills: list[str]
    location: str
    remote_policy: str
    target_company_signals: list[str]
    deal_breakers: list[str]
    raw_text: str


@dataclass
class Candidate:
    """A sourced candidate, normalized to a common shape across all sources."""

    name: str
    current_title: Optional[str] = None
    current_company: Optional[str] = None
    location: Optional[str] = None
    email: Optional[str] = None
    linkedin_url: Optional[str] = None
    github_url: Optional[str] = None
    stackoverflow_url: Optional[str] = None
    sources: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def profile_url_other(self) -> Optional[str]:
        """Non-LinkedIn profile URL, if any — used for the CSV column of the same name."""
        return self.github_url or self.stackoverflow_url


@dataclass
class Score:
    """LLM-assigned score for one candidate against the parsed role."""

    candidate_index: int
    match_score: int
    reason: str
