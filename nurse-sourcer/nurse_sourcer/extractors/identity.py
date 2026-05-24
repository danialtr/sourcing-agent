from __future__ import annotations

import re
import uuid

NAME_NEAR_NURSE_RE = re.compile(
    r"\b([A-Z][a-zA-Z'\-]{1,20}(?:\s+[A-Z][a-zA-Z'\-]{1,20}){0,3})\b[^.\n]{0,40}\b(?:nurse|RN|registered nurse)",
)
NAME_PREFIX_RE = re.compile(
    r"\b(?:I'?m|I am|My name is|This is|Hi[,!]?\s+I'?m)\s+([A-Z][a-zA-Z'\-]{1,20}(?:\s+[A-Z][a-zA-Z'\-]{1,20}){0,2})",
)


def guess_display_name(text: str) -> str | None:
    if not text:
        return None
    match = NAME_PREFIX_RE.search(text)
    if match:
        return match.group(1).strip()
    match = NAME_NEAR_NURSE_RE.search(text)
    if match:
        return match.group(1).strip()
    return None


def make_identity_key(
    *,
    name: str | None = None,
    email: str | None = None,
    handle: str | None = None,
    platform: str | None = None,
) -> str:
    if email:
        return f"email:{email.lower().strip()}"
    if handle and platform:
        return f"{platform.lower()}:{handle.lower().strip()}"
    if handle:
        return f"handle:{handle.lower().strip()}"
    if name:
        normalized = re.sub(r"\s+", " ", name.lower().strip())
        return f"name:{normalized}"
    return f"unknown:{uuid.uuid4()}"
