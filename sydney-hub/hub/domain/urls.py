"""Validation and normalisation of Facebook group URLs. Pure, no I/O."""
from __future__ import annotations

import re
from urllib.parse import urlparse

_HOSTS = {"facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com", "mbasic.facebook.com"}
_SLUG = re.compile(r"^[A-Za-z0-9._-]{3,}$")


class InvalidGroupUrl(ValueError):
    """The value is not a valid Facebook group URL."""


def normalize_group_url(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        raise InvalidGroupUrl("Empty URL")
    if "://" not in value:
        if value.lower().startswith(("javascript:", "data:", "file:")):
            raise InvalidGroupUrl("Unsupported URL scheme")
        value = f"https://{value}"
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").lower() not in _HOSTS:
        raise InvalidGroupUrl("Not a facebook.com URL")
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2 or parts[0].lower() != "groups" or not _SLUG.match(parts[1]):
        raise InvalidGroupUrl("URL must look like facebook.com/groups/<name-or-id>")
    return f"https://www.facebook.com/groups/{parts[1]}/"
