"""Read Workable's public XML job-board feed without depending on OSM employers."""
from __future__ import annotations

from datetime import timezone
from email.utils import parsedate_to_datetime
import zlib
from typing import Iterable, Iterator
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import httpx

from hub.discovery.published import PublishedListing


WORKABLE_FEED_URL = "https://www.workable.com/boards/workable.xml"
DEFAULT_MAX_WIRE_BYTES = 200 * 1024 * 1024
DEFAULT_MAX_NODE_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_KEPT = 10_000
DEFAULT_MAX_STREAM_RECORDS = 300_000
DEFAULT_MAX_INFLATION_RATIO = 16
DEFAULT_MIN_RATIO_CHECK_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_DESCRIPTION_CHARS = 16_384


def _job_listing(job: ET.Element) -> PublishedListing | None:
    def field(name: str) -> str | None:
        return (job.findtext(name) or "").strip() or None

    if (field("country") or "").upper() != "AU":
        return None
    url = field("url")
    parsed = urlsplit(url or "")
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    title, company = field("title"), field("company")
    if not title or not company:
        return None
    raw_date = field("date")
    try:
        parsed_date = parsedate_to_datetime(raw_date) if raw_date else None
        published_at = parsed_date.astimezone(timezone.utc) if parsed_date and parsed_date.tzinfo else None
    except (TypeError, ValueError, IndexError):
        published_at = None
    location = " ".join(part for part in (field("city"), field("state"), field("postalcode")) if part) or None
    description = field("description")
    if description and len(description) > DEFAULT_MAX_DESCRIPTION_CHARS:
        description = description[:DEFAULT_MAX_DESCRIPTION_CHARS]
    return PublishedListing(
        source_id="workable", external_id=field("referencenumber"), source_url=url,
        title=title, company=company, location=location, published_at=published_at,
        employment_type=field("jobtype"), description=description, lat=None, lng=None,
    )


def iter_workable_listings(
    chunks: Iterable[bytes], *, max_bytes: int = 268435456, max_jobs: int = 100000,
    max_node_bytes: int = DEFAULT_MAX_NODE_BYTES, max_kept: int | None = None,
) -> Iterator[PublishedListing]:
    parser = ET.XMLPullParser(events=("start", "end"))
    total = records = kept = since_clear = 0
    root: ET.Element | None = None
    tail = b""
    try:
        for chunk in chunks:
            total += len(chunk)
            since_clear += len(chunk)
            if total > max_bytes:
                raise ValueError("Workable feed byte limit exceeded")
            if since_clear > max_node_bytes:
                raise ValueError("Workable feed element byte limit exceeded")
            scan = (tail + chunk).lower()
            if b"<!doctype" in scan or b"<!entity" in scan:
                raise ValueError("Workable feed XML entities are disallowed")
            tail = scan[-16:]
            parser.feed(chunk)
            for event, node in parser.read_events():
                if root is None and event == "start":
                    if node.tag != "source":
                        raise ValueError("Invalid Workable feed root")
                    root = node
                if event == "end" and node.tag == "job":
                    records += 1
                    if records > max_jobs:
                        raise ValueError("Workable feed record limit exceeded")
                    listing = _job_listing(node)
                    if listing is not None:
                        kept += 1
                        if max_kept is not None and kept > max_kept:
                            raise ValueError("Workable feed kept record limit exceeded")
                        yield listing
                    node.clear()
                    if root is not None:
                        root.clear()
                    since_clear = 0
        parser.close()
        if root is None:
            raise ValueError("Empty Workable feed")
    except ET.ParseError as exc:
        raise ValueError("Malformed Workable feed XML") from exc


def _iter_bounded_gzip_wire(
    response: httpx.Response, *, max_wire_bytes: int, max_inflation_ratio: int, min_ratio_check_bytes: int,
) -> Iterator[bytes]:
    decomp = zlib.decompressobj(16 + zlib.MAX_WBITS)
    wire_total = decompressed_total = 0

    def check_ratio() -> None:
        if decompressed_total >= min_ratio_check_bytes and decompressed_total > wire_total * max_inflation_ratio:
            raise ValueError("Workable feed compression ratio limit exceeded")

    try:
        for raw in response.iter_raw(chunk_size=65536):
            wire_total += len(raw)
            if wire_total > max_wire_bytes:
                raise ValueError("Workable feed wire byte limit exceeded")
            pending = raw
            while pending:
                out = decomp.decompress(pending, 65536)
                decompressed_total += len(out)
                check_ratio()
                if out:
                    yield out
                pending = decomp.unconsumed_tail
        while True:
            tail = decomp.flush(65536)
            if not tail:
                break
            decompressed_total += len(tail)
            check_ratio()
            yield tail
    except zlib.error as exc:
        raise ValueError("Malformed Workable gzip stream") from exc


def fetch_workable(
    client: httpx.Client, *, max_wire_bytes: int = DEFAULT_MAX_WIRE_BYTES,
    max_records: int = DEFAULT_MAX_STREAM_RECORDS, max_kept: int = DEFAULT_MAX_KEPT,
    max_node_bytes: int = DEFAULT_MAX_NODE_BYTES, max_inflation_ratio: int = DEFAULT_MAX_INFLATION_RATIO,
    min_ratio_check_bytes: int = DEFAULT_MIN_RATIO_CHECK_BYTES,
) -> list[PublishedListing]:
    with client.stream("GET", WORKABLE_FEED_URL, follow_redirects=False, timeout=30.0) as response:
        if 300 <= response.status_code < 400:
            raise ValueError("Workable feed redirect refused")
        response.raise_for_status()
        content_length = response.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > max_wire_bytes:
            raise ValueError("Workable feed wire byte limit exceeded")
        encodings = [tok.strip().lower() for tok in response.headers.get("content-encoding", "").split(",") if tok.strip()]
        if encodings == ["gzip"]:
            chunks = _iter_bounded_gzip_wire(
                response, max_wire_bytes=max_wire_bytes,
                max_inflation_ratio=max_inflation_ratio, min_ratio_check_bytes=min_ratio_check_bytes,
            )
            return list(iter_workable_listings(
                chunks, max_bytes=max_wire_bytes * max_inflation_ratio,
                max_jobs=max_records, max_node_bytes=max_node_bytes, max_kept=max_kept,
            ))
        if not encodings:
            return list(iter_workable_listings(
                response.iter_bytes(chunk_size=65536),
                max_bytes=min(268435456, max_wire_bytes), max_jobs=min(100000, max_records),
                max_node_bytes=max_node_bytes, max_kept=max_kept,
            ))
        raise ValueError("Unsupported Workable feed encoding")
