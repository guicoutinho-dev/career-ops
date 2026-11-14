"""Source-grounded career intelligence & application preparation without auto-submission."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from hub.domain.matching import CandidateProfile, MatchAssessment, evaluate_opportunity_match
from hub.domain.specified_work_462 import SpecifiedWork462Assessment, classify_specified_work_462


_TICKET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("RSA", re.compile(r"\brsa\b|responsible service of alcohol", re.IGNORECASE)),
    ("RCG", re.compile(r"\brcg\b|responsible conduct of gambling", re.IGNORECASE)),
    ("White Card", re.compile(r"\bwhite\s*card\b", re.IGNORECASE)),
    ("Forklift", re.compile(r"\bforklift\b|\blf\s*licence\b", re.IGNORECASE)),
    ("First Aid", re.compile(r"\bfirst\s*aid\b|\bcpr\b", re.IGNORECASE)),
    ("Driver Licence", re.compile(r"\bdriver'?s?\s*licen[cs]e\b|\bopen\s*licen[cs]e\b", re.IGNORECASE)),
)


@dataclass(frozen=True)
class CandidateFacts:
    source_files: list[str] = field(default_factory=list)
    name: str | None = None
    visa_status: str | None = None
    suburb: str | None = None
    english_level: str | None = None
    tickets_held: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    experience_highlights: list[str] = field(default_factory=list)
    target_roles: list[str] = field(default_factory=list)
    missing_profile_sources: list[str] = field(default_factory=list)

    def to_candidate_profile(self) -> CandidateProfile:
        return CandidateProfile(
            target_roles=tuple(self.target_roles) if self.target_roles else (
                "barista", "cafe_all_rounder", "kitchen_hand", "cleaner", "housekeeping", "warehouse", "delivery",
            ),
            tickets_held=tuple(self.tickets_held),
            visa_type=self.visa_status or "WHV 462",
            english_level=self.english_level or "intermediate",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_files": list(self.source_files),
            "name": self.name,
            "visa_status": self.visa_status,
            "suburb": self.suburb,
            "english_level": self.english_level,
            "tickets_held": list(self.tickets_held),
            "skills": list(self.skills),
            "languages": list(self.languages),
            "experience_highlights": list(self.experience_highlights),
            "target_roles": list(self.target_roles),
            "missing_profile_sources": list(self.missing_profile_sources),
        }


def _dedupe_str(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        clean = " ".join(str(item or "").split()).strip()
        if clean and clean.lower() not in seen:
            seen.add(clean.lower())
            out.append(clean)
    return out


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value if v]
    return []


def load_candidate_facts(root_dir: Path | None) -> CandidateFacts:
    """Load real user-layer candidate facts (config/profile.yml, cv.md); never loads USER.md or *.example.*."""
    if root_dir is None:
        return CandidateFacts(missing_profile_sources=["config/profile.yml", "cv.md"])

    root = Path(root_dir)
    source_files: list[str] = []
    missing_sources: list[str] = []
    name: str | None = None
    visa_status: str | None = None
    suburb: str | None = None
    english_level: str | None = None
    tickets: list[str] = []
    skills: list[str] = []
    languages: list[str] = []
    highlights: list[str] = []
    target_roles: list[str] = []

    profile_path = root / "config" / "profile.yml"
    if profile_path.is_file():
        source_files.append("config/profile.yml")
        try:
            raw = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            raw = {}
        if isinstance(raw, dict):
            cand = raw.get("candidate") if isinstance(raw.get("candidate"), dict) else raw
            if isinstance(cand, dict):
                name = str(cand.get("name")).strip() if cand.get("name") else None
                visa_status = str(cand.get("visa") or cand.get("visa_status") or "").strip() or None
                suburb = str(cand.get("location") or cand.get("suburb") or "").strip() or None
                english_level = str(cand.get("english_level") or cand.get("english") or "").strip() or None
                tickets.extend(_as_list(cand.get("tickets") or cand.get("certifications")))
                skills.extend(_as_list(cand.get("skills")))
                languages.extend(_as_list(cand.get("languages")))
                target_roles.extend(_as_list(cand.get("target_roles") or cand.get("roles")))
    else:
        missing_sources.append("config/profile.yml")

    doc_path = root / "cv.md"
    if not doc_path.is_file():
        missing_sources.append("cv.md")
    else:
        source_files.append("cv.md")
        text = doc_path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            clean = line.strip()
            if clean.startswith("- ") or clean.startswith("* "):
                bullet = clean[2:].strip()
                if 6 <= len(bullet) <= 140:
                    highlights.append(bullet)
        for label, pat in _TICKET_PATTERNS:
            if pat.search(text):
                tickets.append(label)

    return CandidateFacts(
        source_files=source_files,
        name=name,
        visa_status=visa_status,
        suburb=suburb,
        english_level=english_level,
        tickets_held=_dedupe_str(tickets),
        skills=_dedupe_str(skills),
        languages=_dedupe_str(languages),
        experience_highlights=_dedupe_str(highlights)[:8],
        target_roles=_dedupe_str(target_roles),
        missing_profile_sources=missing_sources if not source_files else [],
    )


def _channel_guidance(verification_state: str) -> str:
    if verification_state == "verified_job":
        return "Verified active job listing: apply directly via the official listing URL after confirming role details and commute."
    if verification_state == "employer_lead":
        return "Proactive employer lead (no confirmed active vacancy): prepare a short speculative enquiry / CV drop directly to the venue or careers page."
    return "Unverified community post: treat as an unconfirmed lead; confirm employer identity, legal pay (ABN/TFN/award), and safety before applying or sharing documents."


def prepare_application_packet(
    opportunity: dict[str, Any],
    candidate: CandidateFacts | None = None,
    *,
    match: MatchAssessment | None = None,
    specified_work: SpecifiedWork462Assessment | None = None,
) -> dict[str, Any]:
    """Assemble a manual-review-only application & outreach packet grounded strictly in saved sources."""
    facts = candidate or CandidateFacts()
    profile = facts.to_candidate_profile()
    eval_match = match or evaluate_opportunity_match(opportunity, profile)
    eval_462 = specified_work or classify_specified_work_462(opportunity)

    verification_state = str(opportunity.get("verification_state") or "unverified_job_post")
    lifecycle_state = str(opportunity.get("lifecycle_state") or "active")
    title = str(opportunity.get("title") or "Role")
    company = str(opportunity.get("company") or "").strip() or None
    suburb = str(opportunity.get("suburb") or opportunity.get("location") or "").strip() or None
    source_url = str(opportunity.get("source_url") or opportunity.get("url") or "").strip() or None

    matched_facts: list[str] = list(eval_match.reasons)
    if facts.visa_status:
        matched_facts.append(f"Candidate visa from profile: {facts.visa_status}")
    if facts.experience_highlights:
        matched_facts.append(f"Grounded CV highlight: {facts.experience_highlights[0]}")

    missing: list[str] = list(eval_match.unknowns)
    req_text = str(opportunity.get("requirements") or "")
    held_lower = " ".join(facts.tickets_held).lower()
    for label, pat in _TICKET_PATTERNS:
        if pat.search(req_text) and label.lower() not in held_lower:
            missing.append(f"Confirm or obtain {label} (mentioned in opportunity requirements, not present in saved candidate facts)")
    if facts.missing_profile_sources:
        missing.append(
            f"Candidate profile files missing ({', '.join(facts.missing_profile_sources)}): verify CV details manually before sending"
        )
    if eval_462.status == "needs_review":
        for item in eval_462.missing_facts:
            missing.append(f"462 88-day check: {item}")

    warnings: list[str] = list(eval_match.suspicious_signals)
    if lifecycle_state == "expired":
        warnings.append("Listing lifecycle_state is expired — verify manually before spending time preparing an application.")
    elif lifecycle_state == "stale" or opportunity.get("is_stale"):
        warnings.append("Evidence is older than 7 days (stale) — confirm the vacancy is still open.")

    sender_name = facts.name or "[Your Name]"
    greeting_target = company or "Hiring Team"
    place_bit = f" in {suburb}" if suburb else ""
    visa_bit = f" I currently hold a {facts.visa_status} visa with work rights." if facts.visa_status else ""
    highlight_bit = (
        f" My relevant background includes: {facts.experience_highlights[0]}."
        if facts.experience_highlights
        else ""
    )

    if verification_state == "employer_lead":
        outreach_draft_en = (
            f"Hi {greeting_target},\n\n"
            f"I live nearby{place_bit} and wanted to check if you have any upcoming casual or part-time openings "
            f"for {title.replace('(OSM discovery)', '').strip() or 'team member'} roles.{visa_bit}{highlight_bit}\n\n"
            f"I have attached my CV and am happy to drop in for a quick chat or trial shift if helpful.\n\n"
            f"Thanks,\n{sender_name}"
        )
        outreach_draft_pt = (
            f"Oi {greeting_target}, tudo bem? Moro perto{place_bit} e gostaria de saber se vocês têm alguma vaga "
            f"casual ou part-time aberta no momento.{visa_bit} Posso enviar meu CV e disponibilidade se ajudar. Obrigado(a), {sender_name}."
        )
    else:
        role_label = title if verification_state == "verified_job" else f"{title} opportunity"
        outreach_draft_en = (
            f"Hi {greeting_target},\n\n"
            f"I am reaching out regarding the {role_label}{place_bit}.{visa_bit}{highlight_bit} "
            f"Could you please confirm availability, award/hourly rate, and next steps to apply?\n\n"
            f"Thanks,\n{sender_name}"
        )
        outreach_draft_pt = (
            f"Oi {greeting_target}, vi a oportunidade de {role_label}{place_bit} e tenho interesse."
            f"{visa_bit} Poderiam confirmar a taxa por hora, carga horária e como prefere receber o currículo? Obrigado(a), {sender_name}."
        )

    evidence_urls = [
        str(ev["source_url"])
        for ev in (opportunity.get("evidence") or [])
        if isinstance(ev, dict) and ev.get("source_url")
    ]

    checklist = [
        "Confirm listing is still open directly at the source URL (auto-submit is disabled).",
        "Review all items in missing_facts_to_confirm and suspicious_warnings before replying.",
        "Attach an accurate CV reflecting only verified experience and held tickets/licences.",
        "Record outcome manually if contacted (never auto-submitted by career-ops).",
    ]

    return {
        "opportunity_id": opportunity.get("id"),
        "title": title,
        "company": company,
        "suburb": suburb,
        "source_url": source_url,
        "verification_state": verification_state,
        "lifecycle_state": lifecycle_state,
        "auto_submit": False,
        "submission_mode": "manual_review_only",
        "channel_guidance": _channel_guidance(verification_state),
        "match": eval_match.to_dict(),
        "specified_work_462": eval_462.to_dict(),
        "matched_facts": matched_facts,
        "missing_facts_to_confirm": list(dict.fromkeys(missing)),
        "suspicious_warnings": list(dict.fromkeys(warnings)),
        "outreach_draft_en": outreach_draft_en,
        "outreach_draft_pt": outreach_draft_pt,
        "checklist": checklist,
        "provenance": {
            "candidate_sources": list(facts.source_files),
            "opportunity_sources": list(dict.fromkeys([u for u in [source_url, *evidence_urls] if u])),
        },
    }
