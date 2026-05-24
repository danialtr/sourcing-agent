from __future__ import annotations

import re

from nurse_sourcer.models import Signals

NURSE_KEYWORDS = [
    "nurse",
    "rn",
    "registered nurse",
    "staff nurse",
    "icu nurse",
    "nars",
    "narsing",
    "krankenpfleger",
    "krankenschwester",
    "pflegefachkraft",
    "pflegekraft",
    "pflegefachmann",
    "pflegefachfrau",
]
COUNTRY_KEYWORDS = [
    "filipino",
    "pinoy",
    "pinay",
    "philippines",
    "manila",
    "cebu",
    "davao",
    "iloilo",
    "quezon",
    "tagalog",
]
GERMANY_KEYWORDS = [
    "germany",
    "deutschland",
    "german",
    "deutsch",
    "berlin",
    "münchen",
    "munich",
    "hamburg",
    "leipzig",
    "frankfurt",
    "stuttgart",
    "köln",
    "cologne",
    "dresden",
]
LANGUAGE_KEYWORDS = ["b1", "b2", "c1", "goethe", "testdaf", "telc", "deutschkurs"]
ICU_KEYWORDS = [
    "icu",
    "intensive care",
    "critical care",
    "its",
    "intensivstation",
    "intensivmedizin",
]
PROGRAM_KEYWORDS = [
    "triple win",
    "anerkennung",
    "anpassungslehrgang",
    "kenntnisprüfung",
    "kenntnispruefung",
    "§16d",
    "16d",
    "fachkräfteeinwanderungsgesetz",
    "anerkennungspartnerschaft",
]

EXPERIENCE_RE = re.compile(
    r"(\d{1,2})\s*\+?\s*(?:years?|yrs?)\s*(?:of)?\s*(?:experience|exp|nursing|practice)",
    re.IGNORECASE,
)


def _contains_any(text: str, words: list[str]) -> bool:
    return any(w in text for w in words)


def extract_signals(text: str) -> Signals:
    text_lower = (text or "").lower()
    match = EXPERIENCE_RE.search(text_lower)
    years = int(match.group(1)) if match else None

    return Signals(
        is_nurse=_contains_any(text_lower, NURSE_KEYWORDS),
        is_filipino=_contains_any(text_lower, COUNTRY_KEYWORDS),
        mentions_germany=_contains_any(text_lower, GERMANY_KEYWORDS),
        mentions_language=_contains_any(text_lower, LANGUAGE_KEYWORDS),
        mentions_icu=_contains_any(text_lower, ICU_KEYWORDS),
        mentions_program=_contains_any(text_lower, PROGRAM_KEYWORDS),
        years_experience=years,
    )
