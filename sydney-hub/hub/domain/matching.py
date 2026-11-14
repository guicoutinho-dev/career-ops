"""Deterministic opportunity matching with explainable reasons, unknowns, and suspicious signals."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Literal


_AU_NATIONAL_MIN_WAGE_HOURLY = 24.10

_ROLE_ALIAS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("barista", re.compile(r"\b(?:baristas?|coffee\s+makers?|espresso(?:\s+maker|\s+staff)?)\b", re.I)),
    ("bartender", re.compile(r"\b(?:bartenders?|bar\s+(?:staff|attendants?|tenders?)|mixologist|cocktail\s+staff)\b", re.I)),
    ("housekeeping", re.compile(r"\b(?:housekeep(?:er|ers|ing)|room\s+attendants?|hotel\s+attendants?)\b", re.I)),
    ("cleaner", re.compile(r"\b(?:(?:commercial\s+)?clean(?:er|ers|ing)|janitor|laundry\s+attendant|faxin(?:a|eiro|eira))\b", re.I)),
    ("kitchen", re.compile(r"\b(?:kitchen\s*hands?|dishhands?|dishwashers?|prep\s+cooks?|cooks?|chefs?|stewards?|bakers?)\b", re.I)),
    ("front_of_house", re.compile(r"\b(?:waitstaff|waiters?|waitresses|food\s+runners?|foh|front\s+of\s+house|front\s+desk|guest\s+service|concierge|porters?|night\s+audit)\b", re.I)),
    ("hospitality", re.compile(r"\b(?:all[- ]?rounders?|cafe\s+staff|venue\s+staff|hospitality(?:\s+staff|\s+team)?|f&b|food\s*(?:&|and)\s*beverage|catering)\b", re.I)),
    ("retail", re.compile(r"\b(?:retail|shop|store|sales)\s+(?:assistants?|attendants?|team\s+members?)\b", re.I)),
    ("warehouse", re.compile(r"\b(?:warehouse\s+(?:picker|packer|hands?|operatives?)|pick(?:er|ing)?\s*(?:/|and|&)\s*pack(?:er|ing)?)\b", re.I)),
    ("farm", re.compile(r"\b(?:farm\s+hands?|fruit\s+pick(?:er|ing)|pack\s*shed|harvest(?:er|ing)?|horticulture)\b", re.I)),
    ("labourer", re.compile(r"\b(?:labourers?|laborers?|construction\s+hands?|site\s+hands?)\b", re.I)),
)

_CORPORATE_EXCLUDE_RE = re.compile(
    r"\b(?:legal\s+counsel|solicitor|software\s+engineer|accountant|actuary|cybersecurity|director|vp)\b",
    re.I,
)

_WHV_ACCEPT_RE = re.compile(r"\b(?:whv|462|417|working\s+holiday(?:\s+makers?)?|backpackers?)\b", re.I)
_WHV_REJECT_RE = re.compile(
    r"\b(?:australian\s+citizens?(?:\s+or\s+(?:permanent\s+residents?|pr))?\s+only|"
    r"citizens?\s*/\s*pr\s+only|pr\s+only|no\s+working\s+holiday|no\s+whv)\b",
    re.I,
)
_ENTRY_LEVEL_RE = re.compile(
    r"\b(?:no\s+experience\s+(?:needed|required)|entry[- ]?level|training\s+provided|full\s+training)\b",
    re.I,
)
_EXPERIENCE_YEARS_RE = re.compile(r"\b(\d+)\+?\s*(?:years?|yrs?)(?:\s+of)?\s+experience\b", re.I)
_ENGLISH_FLUENT_RE = re.compile(r"\b(?:fluent|native|excellent|strong)\s+english\b", re.I)
_ENGLISH_BASIC_RE = re.compile(r"\b(?:basic|conversational|good)\s+english\b", re.I)

_CASH_SIGNAL_RE = re.compile(r"\bcash(?:\s+in\s+hand|\s+only)?\b|\bdinheiro\s+na\s+m[aã]o\b", re.I)
_UNPAID_TRIAL_RE = re.compile(r"\bunpaid\s+trial\b|\bteste\s+sem\s+remunera[çc][aã]o\b|\bteste\s+n[aã]o\s+pago\b", re.I)
_UPFRONT_FEE_RE = re.compile(r"\b(?:upfront\s+fee|pay\s+(?:a\s+)?deposit|training\s+fee|uniform\s+deposit)\b", re.I)
_PASSPORT_HOLD_RE = re.compile(r"\b(?:hold|keep|bring|hand\s+over)\s+(?:your\s+)?passports?\b|\bpassports?\s+to\s+hold\b", re.I)

_ENGLISH_RANK = {"basic": 1, "intermediate": 2, "fluent": 3, "native": 4}


@dataclass(frozen=True)
class CandidateProfile:
    target_roles: tuple[str, ...] = (
        "barista", "bartender", "kitchen", "front_of_house", "hospitality",
        "cleaner", "housekeeping", "retail", "warehouse",
    )
    tickets_held: tuple[str, ...] = ()
    visa_type: str = "WHV 462"
    english_level: Literal["basic", "intermediate", "fluent", "native"] = "intermediate"
    experience_years: float = 0.0
    min_hourly_pay: float = 25.0
    max_commute_minutes: int = 45
    commute_window: str = "tuesday_0900"


@dataclass(frozen=True)
class MatchAssessment:
    score: int | None
    tier: Literal["strong", "possible", "weak", "insufficient_data"]
    matched_role_bucket: str | None
    reasons: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    suspicious_signals: list[str] = field(default_factory=list)
    unresolved_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "tier": self.tier,
            "matched_role_bucket": self.matched_role_bucket,
            "reasons": list(self.reasons),
            "unknowns": list(self.unknowns),
            "suspicious_signals": list(self.suspicious_signals),
            "unresolved_fields": list(self.unresolved_fields),
        }


def resolve_role_alias(title: str | None, description: str | None = None) -> str | None:
    """Resolve a deterministic role bucket from title (or fallback description) aliases."""
    title_text = (title or "").strip()
    if title_text and _CORPORATE_EXCLUDE_RE.search(title_text):
        return None
    for bucket, pattern in _ROLE_ALIAS_PATTERNS:
        if title_text and pattern.search(title_text):
            return bucket
    desc_text = (description or "").strip()[:500]
    if desc_text and not title_text:
        for bucket, pattern in _ROLE_ALIAS_PATTERNS:
            if pattern.search(desc_text):
                return bucket
    return None


def _parse_requirements(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    text = str(raw).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except ValueError:
            pass
    return [part.strip() for part in re.split(r"[;,|]", text) if part.strip()]


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _collect_source_text(opportunity: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("title", "description", "employment_type", "experience_required", "pay_display"):
        val = opportunity.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
    for ev in opportunity.get("evidence") or []:
        if isinstance(ev, dict) and isinstance(ev.get("excerpt"), str) and ev["excerpt"].strip():
            parts.append(ev["excerpt"].strip())
    return "\n".join(parts)


def evaluate_opportunity_match(
    opportunity: dict[str, Any],
    profile: CandidateProfile | None = None,
    *,
    now: datetime | None = None,
) -> MatchAssessment:
    """Score and explain opportunity fit strictly from source-backed fields."""
    prof = profile or CandidateProfile()
    current = now or datetime.now(timezone.utc)
    verification = opportunity.get("verification_state") or "unverified_job_post"
    title = (opportunity.get("title") or "").strip() or None
    desc = (opportunity.get("description") or "").strip()
    source_text = _collect_source_text(opportunity)

    reasons: list[str] = []
    unknowns: list[str] = []
    suspicious: list[str] = []
    unresolved: list[str] = []

    role_bucket = opportunity.get("role_bucket") or resolve_role_alias(title, desc)
    if verification == "employer_lead" and not title and not desc and not opportunity.get("pay_hourly_min"):
        unknowns.extend([
            "Open job title not confirmed (employer lead only)",
            "WHV 462 acceptance not stated",
            "Pay not disclosed",
            "Employment type not stated",
            "Certification requirements not stated",
            "Transit time not measured",
        ])
        if role_bucket:
            reasons.append(f"Category/role hint matches {role_bucket}")
        return MatchAssessment(
            score=None,
            tier="insufficient_data",
            matched_role_bucket=role_bucket,
            reasons=reasons,
            unknowns=unknowns,
            suspicious_signals=suspicious,
            unresolved_fields=[],
        )

    score = 0
    if verification == "verified_job":
        score += 15
        reasons.append("Verified open job listing with checked evidence")
    elif verification == "unverified_job_post":
        reasons.append("Unverified community/source post — confirm live availability directly")
        if not opportunity.get("company") and not opportunity.get("employer_id"):
            suspicious.append("Employer identity not confirmed on unverified post")

    # 1. Role alias match
    if role_bucket:
        if not prof.target_roles or role_bucket in prof.target_roles:
            score += 25
            reasons.append(f"Role matches target bucket '{role_bucket}'")
        else:
            reasons.append(f"Role '{role_bucket}' is outside configured target roles")
    else:
        unknowns.append("Role bucket not matched from stated title")
        unresolved.append("role_bucket")

    # 2. WHV 462 acceptance
    visa_status = (opportunity.get("visa_status") or "unknown").lower()
    if _WHV_REJECT_RE.search(source_text):
        score -= 35
        reasons.append("Visa restriction stated: citizens / PR only (incompatible with WHV 462)")
    elif visa_status in {"whv_mentioned", "whv_eligible", "eligible"} or _WHV_ACCEPT_RE.search(source_text):
        score += 15
        reasons.append("Explicit WHV 462 / Working Holiday acceptance stated")
    else:
        unknowns.append("WHV 462 acceptance not stated")
        unresolved.append("whv_eligible")

    # 3. Entry-level / Experience
    exp_req = (opportunity.get("experience_required") or "").strip()
    exp_years_match = _EXPERIENCE_YEARS_RE.search(f"{exp_req} {desc}")
    if _ENTRY_LEVEL_RE.search(f"{exp_req} {desc}"):
        score += 10
        reasons.append("Entry-level / training provided stated")
    elif exp_years_match:
        req_years = float(exp_years_match.group(1))
        if prof.experience_years >= req_years:
            score += 8
            reasons.append(f"Experience requirement ({req_years:g}y) met")
        else:
            score -= 10
            reasons.append(f"Requires {req_years:g}+ years experience (candidate has {prof.experience_years:g}y)")
    else:
        unknowns.append("Experience requirement not stated")
        unresolved.append("entry_level")

    # 4. Certifications (RSA / RCG / White Card / etc.)
    reqs = _parse_requirements(opportunity.get("requirements"))
    held_lower = {t.casefold() for t in prof.tickets_held}
    if reqs:
        missing_reqs = [r for r in reqs if r.casefold() not in held_lower]
        if not missing_reqs:
            score += 10
            reasons.append(f"Required certifications held: {', '.join(reqs)}")
        else:
            score -= 5
            reasons.append(f"Missing stated certifications: {', '.join(missing_reqs)}")
    else:
        unknowns.append("Certification requirements (RSA/RCG/White Card) not stated")

    # 5. Employment type
    emp_type = (opportunity.get("employment_type") or "").strip()
    if emp_type:
        if emp_type.casefold() in {"casual", "part-time", "temporary", "seasonal"}:
            score += 10
            reasons.append(f"Employment type '{emp_type}' suits flexible WHV schedule")
        else:
            score += 5
            reasons.append(f"Employment type stated: {emp_type}")
    else:
        unknowns.append("Employment type not stated")
        unresolved.append("employment_type")

    # 6. Disclosed pay
    pay_min = opportunity.get("pay_hourly_min")
    pay_display = opportunity.get("pay_display")
    if isinstance(pay_min, (int, float)) and pay_min > 0:
        label = pay_display or f"${pay_min:g}/h"
        if pay_min < _AU_NATIONAL_MIN_WAGE_HOURLY:
            score -= 25
            suspicious.append(
                f"Disclosed pay ({label}) is below AU national minimum wage (${_AU_NATIONAL_MIN_WAGE_HOURLY:.2f}/h)"
            )
        elif pay_min >= prof.min_hourly_pay:
            score += 15
            reasons.append(f"Disclosed pay {label} meets target (${prof.min_hourly_pay:g}/h)")
        else:
            score += 5
            reasons.append(f"Disclosed pay {label} is below target (${prof.min_hourly_pay:g}/h)")
    else:
        unknowns.append("Pay not disclosed")

    # 7. Freshness
    published_dt = _parse_iso(opportunity.get("published_at"))
    if published_dt is not None:
        age_days = max(0.0, (current - published_dt).total_seconds() / 86400.0)
        if age_days <= 7:
            score += 10
            reasons.append(f"Fresh listing ({int(age_days)}d old)")
        elif age_days <= 30:
            score += 5
            reasons.append(f"Recent listing ({int(age_days)}d old)")
        else:
            reasons.append(f"Older listing ({int(age_days)}d since publication)")
    else:
        unknowns.append("Publication date not stated")

    # 8. Measured transit
    commutes = opportunity.get("commute_windows") or {}
    mins = commutes.get(prof.commute_window) if isinstance(commutes, dict) else None
    if isinstance(mins, (int, float)):
        if mins <= prof.max_commute_minutes:
            score += 10
            reasons.append(f"Measured transit {int(mins)} min <= {prof.max_commute_minutes} min")
        else:
            score -= 5
            reasons.append(f"Measured transit {int(mins)} min exceeds {prof.max_commute_minutes} min")
    else:
        unknowns.append("Transit time not measured")

    # 9. English requirement
    cand_rank = _ENGLISH_RANK.get(prof.english_level, 2)
    if _ENGLISH_FLUENT_RE.search(source_text):
        if cand_rank >= 3:
            score += 5
            reasons.append("Fluent English requirement met")
        else:
            score -= 10
            reasons.append("Fluent English required (candidate level is lower)")
    elif _ENGLISH_BASIC_RE.search(source_text):
        score += 5
        reasons.append("Conversational/basic English requirement met")
    else:
        unknowns.append("English level requirement not stated")

    # 10. Suspicious signals
    if _CASH_SIGNAL_RE.search(source_text):
        score -= 20
        suspicious.append("Cash-in-hand payment signal — verify payslips, tax, and super")
    if _UNPAID_TRIAL_RE.search(source_text):
        score -= 20
        suspicious.append("Unpaid trial mentioned — verify Fair Work lawful limits")
    if _UPFRONT_FEE_RE.search(source_text):
        score -= 30
        suspicious.append("Upfront fee or deposit requested")
    if _PASSPORT_HOLD_RE.search(source_text):
        score -= 40
        suspicious.append("Passport/ID retention signal — high risk")

    bounded = max(0, min(100, int(round(score))))
    if bounded >= 70 and not suspicious:
        tier: Literal["strong", "possible", "weak", "insufficient_data"] = "strong"
    elif bounded >= 40:
        tier = "possible"
    else:
        tier = "weak"

    return MatchAssessment(
        score=bounded,
        tier=tier,
        matched_role_bucket=role_bucket,
        reasons=reasons,
        unknowns=unknowns,
        suspicious_signals=suspicious,
        unresolved_fields=unresolved,
    )


def _normalize_ws(text: str) -> str:
    return " ".join((text or "").casefold().split())


def resolve_unresolved_with_llm(
    opportunity: dict[str, Any],
    assessment: MatchAssessment,
    resolver: Callable[[dict[str, Any], list[str]], dict[str, Any]] | None,
) -> MatchAssessment:
    """Resolve only explicitly unresolved fields via an optional LLM hook, strictly source-grounded."""
    if resolver is None or not assessment.unresolved_fields:
        return assessment
    raw = resolver(opportunity, list(assessment.unresolved_fields))
    if not isinstance(raw, dict):
        return assessment

    source_norm = _normalize_ws(_collect_source_text(opportunity))
    reasons = list(assessment.reasons)
    unknowns = list(assessment.unknowns)
    remaining_unresolved = list(assessment.unresolved_fields)
    score = assessment.score if assessment.score is not None else 0

    for field_name, payload in raw.items():
        if field_name not in assessment.unresolved_fields or not isinstance(payload, dict):
            continue
        quote = str(payload.get("evidence_quote") or "").strip()
        if not quote or _normalize_ws(quote) not in source_norm:
            continue
        value = payload.get("value")
        if field_name == "entry_level" and value is True:
            reasons.append(f"Entry level confirmed from source quote: \"{quote}\"")
            unknowns = [u for u in unknowns if "experience" not in u.lower()]
            score = min(100, score + 10)
            remaining_unresolved = [f for f in remaining_unresolved if f != field_name]
        elif field_name == "whv_eligible" and value is True:
            reasons.append(f"WHV acceptance confirmed from source quote: \"{quote}\"")
            unknowns = [u for u in unknowns if "whv" not in u.lower()]
            score = min(100, score + 15)
            remaining_unresolved = [f for f in remaining_unresolved if f != field_name]
        elif field_name == "employment_type" and isinstance(value, str) and value.strip():
            reasons.append(f"Employment type '{value.strip()}' confirmed from source quote: \"{quote}\"")
            unknowns = [u for u in unknowns if "employment type" not in u.lower()]
            score = min(100, score + 8)
            remaining_unresolved = [f for f in remaining_unresolved if f != field_name]

    tier = assessment.tier
    if assessment.score is not None:
        if score >= 70 and not assessment.suspicious_signals:
            tier = "strong"
        elif score >= 40:
            tier = "possible"
        else:
            tier = "weak"

    return replace(
        assessment,
        score=score if assessment.score is not None else assessment.score,
        tier=tier,
        reasons=reasons,
        unknowns=unknowns,
        unresolved_fields=remaining_unresolved,
    )
