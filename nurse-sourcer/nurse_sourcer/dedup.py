from __future__ import annotations

from nurse_sourcer.extractors.identity import make_identity_key
from nurse_sourcer.models import Contacts, Lead, RawHit, Signals


def identity_key_for_hit(hit: RawHit) -> str:
    if hit.contacts.emails:
        return make_identity_key(email=hit.contacts.emails[0])
    if hit.contacts.reddit_usernames:
        return make_identity_key(
            handle=hit.contacts.reddit_usernames[0], platform="reddit"
        )
    if hit.contacts.youtube_channels:
        return make_identity_key(
            handle=hit.contacts.youtube_channels[0], platform="youtube"
        )
    if hit.contacts.tiktok_handles:
        return make_identity_key(
            handle=hit.contacts.tiktok_handles[0], platform="tiktok"
        )
    if hit.contacts.facebook_handles:
        return make_identity_key(
            handle=hit.contacts.facebook_handles[0], platform="facebook"
        )
    if hit.contacts.linkedin_handles:
        return make_identity_key(
            handle=hit.contacts.linkedin_handles[0], platform="linkedin"
        )
    if hit.handle:
        return make_identity_key(handle=hit.handle, platform=hit.source_name)
    if hit.display_name:
        return make_identity_key(name=hit.display_name)
    return make_identity_key()


def _merge_signals(a: Signals, b: Signals) -> Signals:
    return Signals(
        is_nurse=a.is_nurse or b.is_nurse,
        is_filipino=a.is_filipino or b.is_filipino,
        mentions_germany=a.mentions_germany or b.mentions_germany,
        mentions_language=a.mentions_language or b.mentions_language,
        mentions_icu=a.mentions_icu or b.mentions_icu,
        mentions_program=a.mentions_program or b.mentions_program,
        years_experience=max(
            a.years_experience or 0, b.years_experience or 0
        )
        or None,
    )


def _merge_contacts(a: Contacts, b: Contacts) -> Contacts:
    def _u(x: list[str], y: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for v in list(x) + list(y):
            k = v.lower()
            if k in seen:
                continue
            seen.add(k)
            out.append(v)
        return out

    return Contacts(
        emails=_u(a.emails, b.emails),
        linkedin_handles=_u(a.linkedin_handles, b.linkedin_handles),
        facebook_handles=_u(a.facebook_handles, b.facebook_handles),
        whatsapp_numbers=_u(a.whatsapp_numbers, b.whatsapp_numbers),
        tiktok_handles=_u(a.tiktok_handles, b.tiktok_handles),
        youtube_channels=_u(a.youtube_channels, b.youtube_channels),
        reddit_usernames=_u(a.reddit_usernames, b.reddit_usernames),
    )


def _pick_primary_contact(contacts: Contacts) -> tuple[str | None, str | None]:
    # Priority: direct (email, reddit, youtube, tiktok) > FB > LinkedIn URL > whatsapp.
    if contacts.emails:
        return "email", contacts.emails[0]
    if contacts.reddit_usernames:
        return "reddit", contacts.reddit_usernames[0]
    if contacts.youtube_channels:
        return "youtube", contacts.youtube_channels[0]
    if contacts.tiktok_handles:
        return "tiktok", contacts.tiktok_handles[0]
    if contacts.facebook_handles:
        return "facebook", contacts.facebook_handles[0]
    if contacts.linkedin_handles:
        return "linkedin", contacts.linkedin_handles[0]
    if contacts.whatsapp_numbers:
        return "whatsapp", contacts.whatsapp_numbers[0]
    return None, None


def hit_to_lead(hit: RawHit) -> Lead:
    primary_type, primary_value = _pick_primary_contact(hit.contacts)
    return Lead(
        identity_key=identity_key_for_hit(hit),
        display_name=hit.display_name,
        signals=hit.signals,
        contacts=hit.contacts,
        primary_contact_type=primary_type,
        primary_contact_value=primary_value,
        first_seen_at=hit.fetched_at,
        last_seen_at=hit.fetched_at,
        sources=[hit.source_name],
        source_urls=[hit.source_url] if hit.source_url else [],
    )


def merge_into(existing: Lead, hit: RawHit) -> Lead:
    """Merge a new RawHit into an already-existing Lead."""
    merged_contacts = _merge_contacts(existing.contacts, hit.contacts)
    primary_type, primary_value = _pick_primary_contact(merged_contacts)
    sources = existing.sources[:]
    if hit.source_name not in sources:
        sources.append(hit.source_name)
    urls = existing.source_urls[:]
    if hit.source_url and hit.source_url not in urls:
        urls.append(hit.source_url)
    return Lead(
        identity_key=existing.identity_key,
        display_name=existing.display_name or hit.display_name,
        signals=_merge_signals(existing.signals, hit.signals),
        contacts=merged_contacts,
        primary_contact_type=primary_type,
        primary_contact_value=primary_value,
        first_seen_at=min(existing.first_seen_at, hit.fetched_at),
        last_seen_at=max(existing.last_seen_at, hit.fetched_at),
        sources=sources,
        source_urls=urls,
    )


def deduplicate(hits: list[RawHit]) -> list[Lead]:
    leads: dict[str, Lead] = {}
    for hit in hits:
        lead = hit_to_lead(hit)
        if lead.identity_key in leads:
            leads[lead.identity_key] = merge_into(leads[lead.identity_key], hit)
        else:
            leads[lead.identity_key] = lead
    return list(leads.values())
