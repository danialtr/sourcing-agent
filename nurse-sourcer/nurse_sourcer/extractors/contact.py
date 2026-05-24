from __future__ import annotations

import re

from nurse_sourcer.models import Contacts

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
LI_PROFILE_RE = re.compile(r"linkedin\.com/in/([a-zA-Z0-9\-_%]+)", re.IGNORECASE)
FB_PROFILE_RE = re.compile(
    r"facebook\.com/(?!sharer|tr|plugins|dialog|groups|events|pages|pg|watch|marketplace|gaming|business|policies|help|login|signup|recover|reg)([a-zA-Z0-9\.]+)/?",
    re.IGNORECASE,
)
WHATSAPP_RE = re.compile(
    r"(?:whatsapp|wa\.me)[:\s/\-]*(\+?\d[\d\s\-]{8,15}\d)|(\+63\s?\d[\d\s\-]{8,12}\d)",
    re.IGNORECASE,
)
TIKTOK_RE = re.compile(r"tiktok\.com/@([a-zA-Z0-9._]+)", re.IGNORECASE)
YT_CHANNEL_RE = re.compile(
    r"youtube\.com/(?:channel/([A-Za-z0-9_\-]+)|@([A-Za-z0-9_\-\.]+))",
    re.IGNORECASE,
)
REDDIT_USER_RE = re.compile(r"reddit\.com/(?:u|user)/([A-Za-z0-9_\-]+)", re.IGNORECASE)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        key = v.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out


def extract_contacts(text: str, source_url: str = "") -> Contacts:
    haystack = f"{text} {source_url}"

    emails = _dedupe(EMAIL_RE.findall(haystack))
    li = _dedupe(LI_PROFILE_RE.findall(haystack))
    fb = _dedupe(FB_PROFILE_RE.findall(haystack))
    tt = _dedupe(TIKTOK_RE.findall(haystack))
    rd = _dedupe(REDDIT_USER_RE.findall(haystack))

    yt_matches = YT_CHANNEL_RE.findall(haystack)
    yt = _dedupe([m[0] or m[1] for m in yt_matches if (m[0] or m[1])])

    wa_matches = WHATSAPP_RE.findall(haystack)
    wa = _dedupe([(m[0] or m[1]).strip() for m in wa_matches if (m[0] or m[1])])

    # Drop generic Facebook paths that almost never represent a person.
    fb = [h for h in fb if h.lower() not in {"home", "story.php", "permalink.php", "people"}]

    return Contacts(
        emails=emails,
        linkedin_handles=li,
        facebook_handles=fb,
        whatsapp_numbers=wa,
        tiktok_handles=tt,
        youtube_channels=yt,
        reddit_usernames=rd,
    )
