from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Employer(BaseModel):
    name: str
    type: str
    location_city: str
    location_state: str | None = None
    country: str


class Role(BaseModel):
    german_title: str
    english_title: str
    specialty: str
    specialty_aliases: list[str] = Field(default_factory=list)
    trainee_or_qualified: str
    positions_open: int = 1


class Requirements(BaseModel):
    min_years_experience: int = 0
    nursing_qualification: str
    german_level_at_application: str
    german_level_required_within_12_months: str
    english_level: str | None = None
    visa_pathway: str | None = None
    open_to_partial_recognition: bool = True


class TargetSource(BaseModel):
    country: str
    country_aliases: list[str] = Field(default_factory=list)
    preferred_regions: list[str] = Field(default_factory=list)
    exclude_who_red_list: bool = True


class EmployerOffers(BaseModel):
    contract: str | None = None
    starting_salary_eur_month_gross: int | None = None
    full_salary_eur_month_gross: int | None = None
    language_course_funded: bool = False
    housing_assistance_months: int = 0
    flight_reimbursement: bool = False
    family_reunification_supported: bool = False


class Urgency(BaseModel):
    start_date: str | None = None
    shortlist_target_date: str | None = None


class SearchSettings(BaseModel):
    target_total_searches: int = 500
    brave_search_budget: int = 100
    min_score_for_csv: int = 50


class JobBrief(BaseModel):
    job_id: str
    employer: Employer
    role: Role
    requirements: Requirements
    target_source: TargetSource
    employer_offers: EmployerOffers = Field(default_factory=EmployerOffers)
    urgency: Urgency = Field(default_factory=Urgency)
    search_settings: SearchSettings = Field(default_factory=SearchSettings)


class Query(BaseModel):
    text: str
    source: str
    subreddit: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class Signals(BaseModel):
    is_nurse: bool = False
    is_filipino: bool = False
    mentions_germany: bool = False
    mentions_language: bool = False
    mentions_icu: bool = False
    mentions_program: bool = False
    years_experience: int | None = None


class Contacts(BaseModel):
    emails: list[str] = Field(default_factory=list)
    linkedin_handles: list[str] = Field(default_factory=list)
    facebook_handles: list[str] = Field(default_factory=list)
    whatsapp_numbers: list[str] = Field(default_factory=list)
    tiktok_handles: list[str] = Field(default_factory=list)
    youtube_channels: list[str] = Field(default_factory=list)
    reddit_usernames: list[str] = Field(default_factory=list)


class RawHit(BaseModel):
    """One raw result from a single source before dedup."""

    source_name: str
    source_url: str
    title: str | None = None
    text: str = ""
    display_name: str | None = None
    handle: str | None = None
    contacts: Contacts = Field(default_factory=Contacts)
    signals: Signals = Field(default_factory=Signals)
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
    extra: dict[str, Any] = Field(default_factory=dict)


class Lead(BaseModel):
    """A deduplicated lead built from one or more RawHits."""

    identity_key: str
    display_name: str | None = None
    score: int = 0
    signals: Signals = Field(default_factory=Signals)
    contacts: Contacts = Field(default_factory=Contacts)
    primary_contact_type: str | None = None
    primary_contact_value: str | None = None
    first_seen_at: datetime = Field(default_factory=datetime.utcnow)
    last_seen_at: datetime = Field(default_factory=datetime.utcnow)
    sources: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)

    def has_contact_path(self) -> bool:
        return bool(
            self.contacts.emails
            or self.contacts.reddit_usernames
            or self.contacts.facebook_handles
            or self.contacts.youtube_channels
            or self.contacts.tiktok_handles
            or self.contacts.whatsapp_numbers
            or self.contacts.linkedin_handles
        )

    def was_active_recently(self) -> bool:
        delta = datetime.utcnow() - self.last_seen_at
        return delta.days <= 90
