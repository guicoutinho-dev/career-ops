"""Conservative, source-grounded extractor for community job posts. Pure functions, no I/O."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone


_SUBURBS: tuple[str, ...] = (
    "Wolli Creek", "Surry Hills", "Summer Hill", "Dulwich Hill", "St Peters", "Eastgardens",
    "Rosebery", "Kogarah", "Rockdale", "Redfern", "Sydenham", "Marrickville", "Chippendale",
    "Mascot", "Bondi", "Bankstown", "Parramatta", "Newtown", "Strathfield", "Burwood",
    "Ashfield", "Hurstville", "Arncliffe", "Kingsford", "Randwick", "Zetland", "Waterloo",
    "Alexandria", "Glebe", "Pyrmont", "Ultimo", "Haymarket", "Lidcombe", "Auburn", "Homebush",
    "Campsie", "Canterbury", "Lakemba", "Epping", "Chatswood", "Manly", "Coogee", "Maroubra",
    "Botany", "Banksmeadow", "Tempe", "Erskineville", "Enmore", "Petersham", "Lewisham",
    "Penrith", "Liverpool", "Blacktown", "Westmead", "Granville", "Sutherland", "Cronulla",
    "Brighton-Le-Sands", "Banksia", "Carlton", "Allawah", "Hillsdale", "Pagewood", "Eveleigh",
    "Sydney CBD", "Sydney",
)

_ROLE_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("barista", "Barista", re.compile(r"\bbaristas?\b", re.I)),
    ("bartender", "Bartender", re.compile(r"\b(?:bartenders?|bar\s+(?:staff|attendants?|tenders?))\b", re.I)),
    ("kitchen", "Kitchen Hand", re.compile(r"\bkitchen\s+hands?\b", re.I)),
    ("kitchen", "Cook / Chef", re.compile(r"\b(?:cooks?|chefs?|dishwashers?|stewards?|bakers?)\b", re.I)),
    ("front_of_house", "Waitstaff", re.compile(r"\b(?:waitstaff|waiters?|waitresses|gar[çc]om|gar[çc]onete)\b", re.I)),
    ("housekeeping", "Housekeeping", re.compile(r"\b(?:housekeep(?:er|ers|ing)|room\s+attendants?)\b", re.I)),
    ("cleaner", "Cleaner", re.compile(r"\b(?:commercial\s+)?clean(?:er|ers|ing)\b|\bfaxin(?:a|eiro|eira)\b", re.I)),
    ("hospitality", "Hospitality All-Rounder", re.compile(r"\b(?:all[- ]?rounders?|hospitality\s+(?:staff|team))\b", re.I)),
    ("retail", "Retail Assistant", re.compile(r"\b(?:retail|shop|store)\s+(?:assistants?|attendants?|team\s+members?)\b", re.I)),
    ("warehouse", "Warehouse Picker / Packer", re.compile(r"\b(?:warehouse\s+(?:picker|packer|hands?)|pick(?:er|ing)?\s*(?:/|and|&)\s*pack(?:er|ing)?)\b", re.I)),
    ("farm", "Farm / Harvest Worker", re.compile(r"\b(?:farm\s+hands?|fruit\s+pick(?:er|ing)|pack\s*shed|harvest(?:er|ing)?)\b", re.I)),
    ("labourer", "Labourer", re.compile(r"\b(?:labourers?|laborers?|ajudante\s+de\s+obra|constru[çc][aã]o)\b", re.I)),
)

_HOURLY_PAY_RE = re.compile(
    r"\$\s*(\d{2,3}(?:[.,]\d{1,2})?)\s*(?:\+)?\s*(?:/\s*(?:hrs|hr|hour|hora|h)\b|per\s+hour\b|por\s+hora\b)",
    re.I,
)

_EMPLOYMENT_TYPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Casual", re.compile(r"\bcasual\b", re.I)),
    ("Part-time", re.compile(r"\bpart[- ]?time\b", re.I)),
    ("Full-time", re.compile(r"\bfull[- ]?time\b", re.I)),
    ("Seasonal", re.compile(r"\bseasonal\b", re.I)),
    ("Temporary", re.compile(r"\btemporary\b|\btemp\b", re.I)),
)

# Explicit certification/tax-mode mentions only; never infer from role names.
_REQUIREMENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("RSA", re.compile(r"\brsa\b|responsible\s+service\s+of\s+alcohol", re.I)),
    ("RCG", re.compile(r"\brcg\b|responsible\s+conduct\s+of\s+gambling", re.I)),
    ("White Card", re.compile(r"\bwhite\s*card\b", re.I)),
    ("Driver Licence", re.compile(r"\bdriver'?s?\s+licen[cs]e\b|\bcnh\b", re.I)),
    ("ABN", re.compile(r"\babn\b", re.I)),
    ("TFN", re.compile(r"\btfn\b", re.I)),
)

_WHV_RE = re.compile(
    r"\b(?:whv|462|417|working\s+holiday|backpackers?)\b",
    re.I,
)

_PHONE_RE = re.compile(r"(?:\+?61[\s-]?)?0?4\d{2}[\s-]?\d{3}[\s-]?\d{3}\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_INBOX_RE = re.compile(r"\b(?:dm|inbox|pm|direct\s+message|chamar\s+(?:no\s+)?inbox|mandar\s+mensagem)\b", re.I)

_CASH_SIGNAL_RE = re.compile(r"\bcash(?:\s+in\s+hand|\s+only)?\b|\bdinheiro\s+na\s+m[aã]o\b", re.I)
_UNPAID_TRIAL_RE = re.compile(r"\bunpaid\s+trial\b|\bteste\s+sem\s+remunera[çc][aã]o\b|\bteste\s+n[aã]o\s+pago\b", re.I)
_UPFRONT_FEE_RE = re.compile(r"\b(?:upfront\s+fee|pay\s+(?:a\s+)?deposit|training\s+fee|uniform\s+deposit)\b", re.I)
_PASSPORT_HOLD_RE = re.compile(r"\b(?:hold|keep|bring|hand\s+over)\s+(?:your\s+)?passports?\b|\bpassports?\s+to\s+hold\b", re.I)

_AU_NATIONAL_MIN_WAGE_HOURLY = 24.10


@dataclass(frozen=True)
class CommunityExtraction:
    title: str | None
    role_bucket: str | None
    suburb: str | None
    location: str | None
    pay_hourly_min: float | None
    pay_display: str | None
    employment_type: str | None
    requirements: list[str] | None
    whv_eligible: bool | None
    contact: str | None
    contact_excerpt: str | None
    published_at: str | None
    suspicious_signals: list[str] = field(default_factory=list)
    excerpts: dict[str, str] = field(default_factory=dict)


def _snippet(body: str, match: re.Match[str], *, pad: int = 24) -> str:
    start = max(0, match.start() - pad)
    end = min(len(body), match.end() + pad)
    return body[start:end].strip()


def extract_community_post(body: str, *, created_ts: float | None = None) -> CommunityExtraction:
    """Extract only explicit, source-grounded fields and verbatim excerpts from a post body."""
    text = body or ""
    excerpts: dict[str, str] = {}

    title: str | None = None
    role_bucket: str | None = None
    for bucket, label, pattern in _ROLE_PATTERNS:
        m = pattern.search(text)
        if m:
            role_bucket = bucket
            title = label
            excerpts["title"] = m.group(0)
            break

    suburb: str | None = None
    best_suburb_pos: int | None = None
    for candidate in _SUBURBS:
        m = re.search(rf"\b{re.escape(candidate)}\b", text, re.I)
        if m and (best_suburb_pos is None or m.start() < best_suburb_pos):
            best_suburb_pos = m.start()
            suburb = candidate
            excerpts["suburb"] = m.group(0)
    location = f"{suburb}, NSW" if suburb and suburb != "Sydney" else suburb

    pay_hourly_min: float | None = None
    pay_display: str | None = None
    pay_match = _HOURLY_PAY_RE.search(text)
    if pay_match:
        raw_num = pay_match.group(1).replace(",", ".")
        try:
            val = float(raw_num)
            if 10.0 <= val <= 200.0:
                pay_hourly_min = val
                pay_display = pay_match.group(0).strip()
                excerpts["pay"] = pay_match.group(0)
        except ValueError:
            pass

    employment_type: str | None = None
    for label, pattern in _EMPLOYMENT_TYPES:
        m = pattern.search(text)
        if m:
            employment_type = label
            excerpts["employment_type"] = m.group(0)
            break

    reqs: list[str] = []
    req_spans: list[str] = []
    for label, pattern in _REQUIREMENT_PATTERNS:
        m = pattern.search(text)
        if m:
            reqs.append(label)
            req_spans.append(m.group(0))
    requirements = reqs if reqs else None
    if req_spans:
        excerpts["requirements"] = req_spans[0]

    whv_eligible: bool | None = None
    whv_match = _WHV_RE.search(text)
    if whv_match:
        whv_eligible = True
        excerpts["whv_eligible"] = whv_match.group(0)

    contact: str | None = None
    contact_excerpt: str | None = None
    phone_m = _PHONE_RE.search(text)
    email_m = _EMAIL_RE.search(text)
    inbox_m = _INBOX_RE.search(text)
    if phone_m:
        contact = phone_m.group(0).strip()
        contact_excerpt = _snippet(text, phone_m)
        excerpts["contact"] = phone_m.group(0)
    elif email_m:
        contact = email_m.group(0).strip()
        contact_excerpt = _snippet(text, email_m)
        excerpts["contact"] = email_m.group(0)
    elif inbox_m:
        contact_excerpt = _snippet(text, inbox_m)
        excerpts["contact"] = inbox_m.group(0)

    suspicious: list[str] = []
    if _CASH_SIGNAL_RE.search(text):
        if pay_hourly_min is not None and pay_hourly_min < _AU_NATIONAL_MIN_WAGE_HOURLY:
            suspicious.append(f"Cash-in-hand & below national minimum wage (${pay_hourly_min:.2f}/h < ${_AU_NATIONAL_MIN_WAGE_HOURLY:.2f}/h)")
        else:
            suspicious.append("Cash-in-hand payment mentioned — confirm payslips/super/tax")
    elif pay_hourly_min is not None and pay_hourly_min < _AU_NATIONAL_MIN_WAGE_HOURLY:
        suspicious.append(f"Below national minimum wage (${pay_hourly_min:.2f}/h < ${_AU_NATIONAL_MIN_WAGE_HOURLY:.2f}/h)")
    if _UNPAID_TRIAL_RE.search(text):
        suspicious.append("Unpaid trial mentioned — verify Fair Work lawful limits")
    if _UPFRONT_FEE_RE.search(text):
        suspicious.append("Upfront fee or deposit requested")
    if _PASSPORT_HOLD_RE.search(text):
        suspicious.append("Passport/ID retention signal — high risk")

    published_at: str | None = None
    if created_ts is not None and created_ts > 0:
        published_at = datetime.fromtimestamp(created_ts, timezone.utc).isoformat()

    return CommunityExtraction(
        title=title,
        role_bucket=role_bucket,
        suburb=suburb,
        location=location,
        pay_hourly_min=pay_hourly_min,
        pay_display=pay_display,
        employment_type=employment_type,
        requirements=requirements,
        whv_eligible=whv_eligible,
        contact=contact,
        contact_excerpt=contact_excerpt,
        published_at=published_at,
        suspicious_signals=suspicious,
        excerpts=excerpts,
    )
