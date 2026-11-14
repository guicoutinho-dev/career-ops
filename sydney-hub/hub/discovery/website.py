"""Bounded inspection of public employer websites; page text is evidence, never instructions."""
from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx

from hub.discovery.models import SiteInspection, WebsiteFinding

_ROLE = re.compile(
    r"\b(baristas?|bartenders?|bar staff|kitchen hands?|cooks?|chefs?|"
    r"waitstaff|waiters?|waitresses|hospitality all[- ]rounders?|cafe all[- ]rounders?|"
    r"commercial cleaners?|cleaners?|housekeep(?:er|ers|ing)|room attendants?|"
    r"front desk(?: attendants?)?|retail assistants?)\b",
    re.I,
)
_HOSPITALITY_RECEPTION = re.compile(r"\breceptionists?\b", re.I)
_HOSPITALITY_CTX = re.compile(r"\b(hotel|motel|hostel|guest[- ]house|restaurant|cafe|bistro|pub|bar|hospitality|venue)\b", re.I)
_HIRING = re.compile(r"\b(?:we are hiring|we're hiring|now hiring|vacanc(?:y|ies)|join our team)\b", re.I)
_CAREER = re.compile(r"career|vacanc|/jobs?(?:/|$)|work.with.us|join.our.team|employment", re.I)
_APPLY = re.compile(
    r"\bapply(?: now| here| online| for)?\b|submit (?:your )?application|"
    r"\b(?:email|send) (?:your )?(?:resume|cv)\b",
    re.I,
)
_CLOSED = re.compile(r"applications? closed|position filled|no longer accepting applications|vacancy closed", re.I)
_EVERGREEN = re.compile(r"always hiring|speculative application|expressions? of interest|talent pool|future opportunities", re.I)
_MAX_BYTES = 2_000_000
_MAX_PAGES = 6
_MAX_REDIRECTS = 3
_FALLBACK_CAREER_PATHS = ("/careers", "/jobs")


class _BlockedDestination(Exception):
    pass


def _is_public_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        address.is_global
        and not address.is_multicast
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_reserved
        and not address.is_unspecified
    )


class _PublicBackend(httpcore.SyncBackend):
    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            addresses = [ipaddress.ip_address(row[4][0]) for row in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]
        except (OSError, ValueError) as exc:
            raise _BlockedDestination("DNS lookup failed") from exc
        if not addresses or not all(_is_public_ip(address) for address in addresses):
            raise _BlockedDestination("Destination is not public")
        last_exc: OSError | None = None
        for address in addresses:
            try:
                return super().connect_tcp(
                    str(address),
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except OSError as exc:
                last_exc = exc
        assert last_exc is not None
        raise last_exc


def safe_website_client() -> httpx.Client:
    transport = httpx.HTTPTransport(trust_env=False, retries=0)
    # shortcut: httpx has no resolver hook; pin httpcore 1.x backend here until one is exposed.
    transport._pool._network_backend = _PublicBackend()
    return httpx.Client(transport=transport, trust_env=False, follow_redirects=False)


@dataclass
class _Section:
    headings: list[str] = field(default_factory=list)
    text: list[str] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.site_name = ""
        self.h1: list[str] = []
        self.text: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.iframe_srcs: list[str] = []
        self.sections: list[_Section] = []
        self._tag = ""
        self._heading_tag = ""
        self._href: str | None = None
        self._anchor: list[str] = []
        self._hidden_depth = 0
        self._footer_depth = 0
        self._section_stack: list[_Section] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._hidden_depth += 1
            return
        if self._hidden_depth > 0:
            return
        if tag == "footer":
            self._footer_depth += 1
            return
        attrs_map = dict(attrs)
        if tag == "meta" and attrs_map.get("property") == "og:site_name":
            self.site_name = attrs_map.get("content") or ""
        if tag == "iframe":
            src = (attrs_map.get("src") or "").strip()
            if src:
                self.iframe_srcs.append(src)
        if tag in {"section", "article"} and self._footer_depth == 0:
            sec = _Section()
            self._section_stack.append(sec)
            self.sections.append(sec)
        if tag == "a":
            self._href = attrs_map.get("href")
            self._anchor = []
        if tag in {"title", "h1"}:
            self._tag = tag
        if tag in {"h1", "h2", "h3"} and self._footer_depth == 0:
            self._heading_tag = tag

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"}:
            if self._hidden_depth > 0:
                self._hidden_depth -= 1
            return
        if self._hidden_depth > 0:
            return
        if tag == "footer":
            if self._footer_depth > 0:
                self._footer_depth -= 1
            return
        if tag in {"section", "article"} and self._section_stack:
            self._section_stack.pop()
        if tag == "a" and self._href is not None:
            link = (self._href, " ".join(self._anchor).strip())
            self.links.append(link)
            if self._section_stack and self._footer_depth == 0:
                self._section_stack[-1].links.append(link)
            self._href = None
        if tag == self._tag:
            self._tag = ""
        if tag == self._heading_tag:
            self._heading_tag = ""

    def handle_data(self, data: str) -> None:
        if self._hidden_depth > 0:
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
        if self._section_stack and self._footer_depth == 0:
            sec = self._section_stack[-1]
            sec.text.append(clean)
            if self._heading_tag in {"h1", "h2", "h3"}:
                sec.headings.append(clean)


def _public_url(url: str) -> bool:
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or "@" in parts.netloc
        or parts.username
        or parts.password
        or parts.port not in {None, 80, 443}
    ):
        return False
    host = parts.hostname
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = [
                ipaddress.ip_address(row[4][0])
                for row in socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80))
            ]
        except (OSError, ValueError):
            return False
    return bool(addresses) and all(_is_public_ip(address) for address in addresses)


def _same_site(url: str, root_host: str) -> bool:
    return (urlsplit(url).hostname or "").removeprefix("www.") == root_host.removeprefix("www.")


def _match_role(text: str, *, full_context: str = "") -> str | None:
    match = _ROLE.search(text)
    if match:
        return match.group(0)
    rec = _HOSPITALITY_RECEPTION.search(text)
    if rec and _HOSPITALITY_CTX.search(full_context or text):
        return rec.group(0)
    return None


def _has_apply_action(links: list[tuple[str, str]], page_url: str) -> bool:
    for href, label in links:
        if not href or href.startswith("#"):
            continue
        resolved = urljoin(page_url, href)
        scheme = urlsplit(resolved).scheme.lower()
        if scheme not in {"http", "https", "mailto"}:
            continue
        if _APPLY.search(label) or (scheme == "mailto" and re.search(r"resume|cv|apply|job|career", label, re.I)):
            return True
    return False


def _fetch(url: str, client: httpx.Client, root_host: str) -> tuple[str | None, str, str, int, str | None]:
    requests = 0
    redirected_offsite = False
    for _ in range(_MAX_REDIRECTS + 1):
        if not _same_site(url, root_host):
            external = url if redirected_offsite and _public_url(url) else None
            return None, url, "blocked", requests, external
        if not _public_url(url):
            return None, url, "blocked", requests, None
        prev_scheme = urlsplit(url).scheme.lower()
        try:
            with client.stream("GET", url, follow_redirects=False, timeout=8) as response:
                requests += 1
                if response.status_code in {301, 302, 303, 307, 308}:
                    target = response.headers.get("location")
                    if not target:
                        return None, url, "error", requests, None
                    next_url = urljoin(url, target)
                    if prev_scheme == "https" and urlsplit(next_url).scheme.lower() == "http":
                        return None, next_url, "blocked", requests, None
                    url = next_url
                    redirected_offsite = not _same_site(url, root_host)
                    continue
                if response.status_code in {401, 403, 429}:
                    return None, url, "blocked", requests, None
                if response.status_code != 200 or "html" not in response.headers.get("content-type", "").lower():
                    return None, url, "error", requests, None
                chunks, total = [], 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > _MAX_BYTES:
                        return None, url, "error", requests, None
                    chunks.append(chunk)
                return b"".join(chunks).decode("utf-8", errors="replace"), str(response.url), "ok", requests, None
        except _BlockedDestination:
            return None, url, "blocked", requests, None
        except httpx.TimeoutException:
            return None, url, "timeout", requests + 1, None
        except httpx.HTTPError:
            return None, url, "error", requests + 1, None
    return None, url, "error", requests, None


def inspect_website(website_url: str, *, client: httpx.Client, checked_at: float) -> SiteInspection:
    parts = urlsplit(website_url)
    root_host = parts.hostname or ""
    root_url = f"{parts.scheme}://{parts.netloc}/" if parts.scheme in {"http", "https"} and parts.netloc else ""
    queue: list[tuple[str, bool]] = [(website_url, False)]

    seen: set[str] = set()
    findings: list[WebsiteFinding] = []
    outbound_urls: list[str] = []
    outbound_seen: set[str] = set()
    requests = 0
    name: str | None = None
    outcome = "ok"
    any_ok = False
    found_career_link = False
    fallback_added = False

    def record_outbound(raw_url: str) -> None:
        if not raw_url or raw_url.startswith("#"):
            return
        parsed = urlsplit(raw_url)
        if parsed.scheme.lower() in {"http", "https"} and parsed.hostname and not _same_site(raw_url, root_host):
            if raw_url not in outbound_seen:
                outbound_seen.add(raw_url)
                outbound_urls.append(raw_url)

    while len(seen) < _MAX_PAGES:
        if not queue:
            if not any_ok or fallback_added or found_career_link or not root_url or any(f.verification_state == "verified_job" for f in findings):
                break
            fallback_added = True
            candidates = ([root_url] if parts.path.strip("/") else []) + [
                urljoin(root_url, suffix) for suffix in _FALLBACK_CAREER_PATHS
            ]
            for cand in candidates:
                if cand not in seen:
                    queue.append((cand, True))
            if not queue:
                break

        url, is_speculative = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        html, final_url, status, count, external_redirect = _fetch(url, client, root_host)
        requests += count
        if external_redirect:
            record_outbound(external_redirect)
        if status != "ok" or html is None:
            if not is_speculative and outcome == "ok":
                outcome = status
            continue
        if not _same_site(final_url, root_host):
            record_outbound(final_url)
            if not is_speculative and outcome == "ok":
                outcome = "blocked"
            continue

        any_ok = True
        page = _Page()
        page.feed(html)
        if not name:
            raw_name = page.site_name.strip() or page.title.strip()
            name = next(
                (
                    part.strip()
                    for part in re.split(r"\s+[|–—-]\s+", raw_name)
                    if part.strip().casefold() not in {"home", "careers", "jobs", "welcome"}
                ),
                None,
            )

        for src in page.iframe_srcs:
            record_outbound(urljoin(final_url, src))

        text = " ".join(page.text)
        heading = " ".join(page.h1)
        page_verified = False

        for sec in page.sections:
            sec_heading = " ".join(sec.headings)
            sec_text = " ".join(sec.text)
            sec_role = _match_role(sec_heading, full_context=text)
            if (
                sec_role
                and _has_apply_action(sec.links, final_url)
                and not _CLOSED.search(sec_text)
                and not _EVERGREEN.search(sec_text)
                and not _CAREER.search(sec_heading)
            ):
                findings.append(WebsiteFinding("verified_job", final_url, name, sec_heading[:120], sec_text[:400], checked_at))
                page_verified = True

        if not page_verified:
            role = _match_role(heading, full_context=text)
            apply_action = _has_apply_action(page.links, final_url)
            if role and apply_action and not _CLOSED.search(text) and not _EVERGREEN.search(text) and not _CAREER.search(heading):
                findings.append(WebsiteFinding("verified_job", final_url, name, heading[:120], text[:400], checked_at))
            elif _HIRING.search(text):
                text_role = role or _match_role(text, full_context=text)
                if text_role:
                    findings.append(WebsiteFinding("unverified_job_post", final_url, name, text_role, text[:400], checked_at))

        for href, label in page.links:
            if not href or href.startswith("#"):
                continue
            target = urljoin(final_url, href)
            if not _same_site(target, root_host):
                record_outbound(target)
            elif _CAREER.search(href + " " + label):
                found_career_link = True
                if target not in seen and all(q_url != target for q_url, _ in queue):
                    queue.append((target, False))

    if name and not findings and (any_ok or outcome == "ok"):
        findings.insert(0, WebsiteFinding("employer_lead", website_url, name, None, None, checked_at))
    return SiteInspection(outcome, findings, requests, tuple(outbound_urls))
