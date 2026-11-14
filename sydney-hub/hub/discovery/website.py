"""Bounded inspection of public employer websites; page text is evidence, never instructions."""
from __future__ import annotations

import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx
import httpcore

from hub.discovery.models import SiteInspection, WebsiteFinding

_ROLE = re.compile(
    r"\b(baristas?|bartenders?|kitchen hands?|waitstaff|waiters?|waitresses|"
    r"hospitality all[- ]rounder|cafe all[- ]rounder|cleaner|housekeep(?:er|ing)|retail assistant)\b",
    re.I,
)
_HIRING = re.compile(r"\b(?:we are hiring|we're hiring|now hiring|vacanc(?:y|ies)|join our team)\b", re.I)
_CAREER = re.compile(r"career|vacanc|/jobs?(?:/|$)|work.with.us|join.our.team|employment", re.I)
_APPLY = re.compile(r"\bapply(?: now| here| online| for)?\b|submit (?:your )?application", re.I)
_CLOSED = re.compile(r"applications? closed|position filled|no longer accepting applications|vacancy closed", re.I)
_EVERGREEN = re.compile(r"always hiring|speculative application|expressions? of interest|talent pool|future opportunities", re.I)
_MAX_BYTES = 256_000
_MAX_PAGES = 4
_MAX_REDIRECTS = 3


class _BlockedDestination(Exception):
    pass


class _PublicBackend(httpcore.SyncBackend):
    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            addresses = [ipaddress.ip_address(row[4][0]) for row in
                         socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]
        except (OSError, ValueError) as exc:
            raise _BlockedDestination('DNS lookup failed') from exc
        if not addresses or not all(address.is_global for address in addresses):
            raise _BlockedDestination('Destination is not public')
        return super().connect_tcp(str(addresses[0]), port, timeout=timeout,
                                   local_address=local_address, socket_options=socket_options)


def safe_website_client() -> httpx.Client:
    transport = httpx.HTTPTransport(trust_env=False, retries=0)
    # shortcut: httpx has no resolver hook; pin httpcore 1.x backend here until one is exposed.
    transport._pool._network_backend = _PublicBackend()
    return httpx.Client(transport=transport, trust_env=False, follow_redirects=False)


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.site_name = ""
        self.h1: list[str] = []
        self.text: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._tag = ""
        self._href: str | None = None
        self._anchor: list[str] = []
        self._hidden = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._hidden = True
            return
        if self._hidden:
            return
        attrs_map = dict(attrs)
        if tag == "meta" and attrs_map.get("property") == "og:site_name":
            self.site_name = attrs_map.get("content") or ""
        if tag == "a":
            self._href = attrs_map.get("href")
            self._anchor = []
        if tag in {"title", "h1"}:
            self._tag = tag

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"}:
            self._hidden = False
            return
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(self._anchor).strip()))
            self._href = None
        if tag == self._tag:
            self._tag = ""

    def handle_data(self, data: str) -> None:
        if self._hidden:
            return
        clean = " ".join(data.split())
        if not clean:
            return
        self.text.append(clean)
        if self._tag == "title":
            self.title += clean
        elif self._tag == "h1":
            self.h1.append(clean)
        if self._href is not None:
            self._anchor.append(clean)


def _public_url(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        return False
    host = parts.hostname
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = [ipaddress.ip_address(row[4][0]) for row in socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80))]
        except (OSError, ValueError):
            return False
    return bool(addresses) and all(address.is_global for address in addresses)


def _same_site(url: str, root_host: str) -> bool:
    return (urlsplit(url).hostname or "").removeprefix("www.") == root_host.removeprefix("www.")


def _fetch(url: str, client: httpx.Client, root_host: str) -> tuple[str | None, str, str, int]:
    requests = 0
    for _ in range(_MAX_REDIRECTS + 1):
        if not _same_site(url, root_host) or not _public_url(url):
            return None, url, "blocked", requests
        try:
            with client.stream("GET", url, follow_redirects=False, timeout=8) as response:
                requests += 1
                if response.status_code in {301, 302, 303, 307, 308}:
                    target = response.headers.get("location")
                    if not target:
                        return None, url, "error", requests
                    url = urljoin(url, target)
                    continue
                if response.status_code in {401, 403, 429}:
                    return None, url, "blocked", requests
                if response.status_code != 200 or "html" not in response.headers.get("content-type", "").lower():
                    return None, url, "error", requests
                chunks, total = [], 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > _MAX_BYTES:
                        return None, url, "error", requests
                    chunks.append(chunk)
                return b"".join(chunks).decode("utf-8", errors="replace"), str(response.url), "ok", requests
        except _BlockedDestination:
            return None, url, "blocked", requests
        except httpx.TimeoutException:
            return None, url, "timeout", requests + 1
        except httpx.HTTPError:
            return None, url, "error", requests + 1
    return None, url, "error", requests


def inspect_website(website_url: str, *, client: httpx.Client, checked_at: float) -> SiteInspection:
    root_host = urlsplit(website_url).hostname or ""
    queue = [website_url]
    seen: set[str] = set()
    findings: list[WebsiteFinding] = []
    requests = 0
    name: str | None = None
    outcome = "ok"
    while queue and len(seen) < _MAX_PAGES:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        html, final_url, status, count = _fetch(url, client, root_host)
        requests += count
        if status != "ok" or html is None:
            if outcome == "ok":
                outcome = status
            continue
        if not _same_site(final_url, root_host):
            if outcome == "ok":
                outcome = "blocked"
            continue
        page = _Page()
        page.feed(html)
        if not name:
            raw_name = page.site_name.strip() or page.title.strip()
            name = next((part.strip() for part in re.split(r"\s+[|–—-]\s+", raw_name)
                         if part.strip().casefold() not in {"home", "careers", "jobs", "welcome"}), None)
        text = " ".join(page.text)
        heading = " ".join(page.h1)
        role = _ROLE.search(heading)
        apply_action = any(_APPLY.search(label) and href and not href.startswith("#") and
                           urlsplit(urljoin(final_url, href)).scheme in {"http", "https", "mailto"}
                           for href, label in page.links)
        if role and apply_action and not _CLOSED.search(text) and not _EVERGREEN.search(text) and not _CAREER.search(heading):
            findings.append(WebsiteFinding("verified_job", final_url, name, heading[:120], text[:400], checked_at))
        elif _HIRING.search(text) and _ROLE.search(text):
            findings.append(WebsiteFinding("unverified_job_post", final_url, name, role.group(0) if role else None, text[:400], checked_at))
        for href, label in page.links:
            target = urljoin(final_url, href)
            if _same_site(target, root_host) and _CAREER.search(href + " " + label) and target not in seen and target not in queue:
                queue.append(target)
    if name and not any(f.verification_state == "employer_lead" for f in findings):
        findings.insert(0, WebsiteFinding("employer_lead", website_url, name, None, None, checked_at))
    return SiteInspection(outcome, findings, requests)
