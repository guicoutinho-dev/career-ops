import os
import json
import shutil
import hashlib
import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
from pydantic import BaseModel, Field

from src.database.connection import get_connection
from src.services.approval_service import ApprovalService
from src.services.event_logger import EventLogger
from src.services.candidate_profile import load_candidate_profile

class ApplicationPackageAnswers(BaseModel):
    application_id: str
    candidate_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    work_authorization_status: str = ""
    requires_sponsorship: str = ""
    notice_period_availability: str = ""
    salary_expectation: str = ""
    relocation_declaration: str = ""
    technical_highlights: List[str] = Field(default_factory=list)
    answers_by_field: Dict[str, str] = Field(default_factory=dict)

class ApplicationPreparer:
    """Prepares verified application answers, pre-submission packages, and enforces submission barriers."""

    def __init__(
        self,
        db_path: Path | str = "data/applications.db",
        profile_path: Path | str = "config/profile.yml",
        applications_dir: Path | str = "applications",
    ):
        self.db_path = db_path
        self.profile_path = profile_path
        self.applications_dir = Path(applications_dir)
        self.approval_service = ApprovalService(db_path, applications_dir=self.applications_dir)

    def prepare_answers(self, application_id: str) -> ApplicationPackageAnswers:
        """Prepares pre-filled answers derived strictly from candidate profile and verified CV."""
        profile = load_candidate_profile(self.profile_path)
        candidate = profile.get("candidate") or {}
        location = profile.get("location") or {}
        compensation = profile.get("compensation") or {}
        narrative = profile.get("narrative") or {}
        name = candidate.get("full_name", "")
        name_parts = name.split()
        answers = ApplicationPackageAnswers(
            application_id=application_id,
            candidate_name=name,
            email=candidate.get("email", ""),
            phone=candidate.get("phone", ""),
            location=location.get("city", ""),
            linkedin=candidate.get("linkedin", ""),
            work_authorization_status=location.get("visa_status", ""),
            requires_sponsorship=candidate.get("requires_sponsorship", ""),
            notice_period_availability=candidate.get("availability", ""),
            salary_expectation=compensation.get("target_range", ""),
            relocation_declaration=location.get("flexibility", ""),
            technical_highlights=narrative.get("superpowers", []),
        )
        answers.answers_by_field = {
            "first_name": name_parts[0] if name_parts else "",
            "last_name": " ".join(name_parts[1:]),
            "email": answers.email,
            "phone": answers.phone,
            "linkedin": answers.linkedin,
            "work_rights": answers.work_authorization_status,
            "sponsorship_required": answers.requires_sponsorship,
            "availability": answers.notice_period_availability,
            "salary_expectation": answers.salary_expectation,
            "relocation": answers.relocation_declaration
        }
        return answers

    def assemble_submission_package(self, application_id: str) -> Dict[str, Any]:
        """
        Creates the complete pre-submission package in applications/<app_id>/
        including answers.json, checklist.md, and document references.
        """
        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT a.id, a.status, a.match_score, j.employer, j.title, j.canonical_url, j.location, j.description
                FROM applications a
                JOIN jobs j ON a.job_id = j.id
                WHERE a.id = ?;
            """, (application_id,))
            app_row = cursor.fetchone()
            if not app_row:
                raise ValueError(f"Application '{application_id}' not found.")

            app_dir = self.approval_service.resolve_application_dir(application_id)
            app_dir.mkdir(parents=True, exist_ok=True)

            # 1. Write job_description.md
            jd_path = app_dir / "job_description.md"
            jd_content = f"# {app_row['title']} — {app_row['employer']}\n\n**Location:** {app_row['location']}\n**URL:** {app_row['canonical_url']}\n\n## Description\n{app_row['description']}\n"
            jd_path.write_text(jd_content, encoding="utf-8")

            # 2. Prepare and write answers.json
            answers = self.prepare_answers(application_id)
            answers_path = app_dir / "answers.json"
            answers_path.write_text(json.dumps(answers.model_dump(), indent=2), encoding="utf-8")

            # 3. Locate existing generated CV and cover letter in output/drafts/
            drafts_dir = Path("output/drafts")
            cv_found = None
            cover_found = None

            if drafts_dir.exists():
                for sub in drafts_dir.iterdir():
                    if sub.is_dir():
                        m_file = sub / "manifest.json"
                        if m_file.exists():
                            try:
                                m = json.loads(m_file.read_text(encoding="utf-8"))
                                if m.get("job", {}).get("url") == app_row["canonical_url"]:
                                    for f in sub.glob("cv*.pdf"):
                                        cv_found = str(f)
                                        break
                                    for f in sub.glob("cover-letter*.pdf"):
                                        cover_found = str(f)
                                        break
                                    break
                            except Exception:
                                pass

            for source in (cv_found, cover_found):
                if source and Path(source).is_file():
                    shutil.copy2(source, app_dir / Path(source).name)

            # 4. Generate checklist.md
            checklist_path = app_dir / "checklist.md"
            checklist_content = f"""# Pre-Submission Checklist — {app_row['employer']}

**Application ID:** `{application_id}`
**Role:** {app_row['title']}
**Application URL:** [{app_row['canonical_url']}]({app_row['canonical_url']})
**Current Status:** `{app_row['status']}`

---

## 1. Document Attachments
- [ ] **CV PDF Attached:** `{cv_found or 'Pending generation'}`
- [ ] **Cover Letter PDF Attached:** `{cover_found or 'Pending generation'}`

## 2. Standard Responses (Pre-Verified)
- **Legal Work Rights:** `{answers.work_authorization_status}`
- **Sponsorship Needed:** `{answers.requires_sponsorship}`
- **Notice Period / Start Date:** `{answers.notice_period_availability}`
- **Remuneration Target:** `{answers.salary_expectation}`
- **Relocation Intent:** `{answers.relocation_declaration}`

## 3. Human Approval Gate
- [ ] Explicit Human Approval Token Issued (`python -m src.cli.approve {application_id}`)
- [ ] Manual final review of portal form before clicking Submit

> ⚠️ **MANDATORY POLICY:** Unattended workers are physically blocked from submitting this form. Submission must be performed or finalized with explicit operator presence.
"""
            checklist_path.write_text(checklist_content, encoding="utf-8")

            # 5. Log audit event
            logger = EventLogger(conn)
            logger.log_event(
                entity_type="application",
                entity_id=application_id,
                event_type="PACKAGE_PREPARED",
                actor="system:ApplicationPreparer",
                correlation_id=f"prep_{application_id}",
                payload={"checklist_path": str(checklist_path), "status": app_row["status"]}
            )

            return {
                "application_id": application_id,
                "package_dir": str(app_dir),
                "checklist_path": str(checklist_path),
                "answers_path": str(answers_path),
                "cv_path": cv_found,
                "cover_letter_path": cover_found
            }
        finally:
            conn.close()

    def attempt_submission(self, application_id: str, operator_token: Optional[str] = None) -> Tuple[bool, str]:
        """
        Enforces the mandatory manual approval barrier.
        Raises PermissionError if application is not APPROVED or token is invalid.
        """
        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM applications WHERE id = ?;", (application_id,))
            row = cursor.fetchone()
            if not row:
                raise ValueError(f"Application '{application_id}' not found.")

            status = row["status"]
            if status != "APPROVED":
                raise PermissionError(
                    f"SUBMISSION BLOCKED: Application '{application_id}' has status '{status}'. "
                    f"Only applications with verified 'APPROVED' status and cryptographic token may be submitted."
                )

            # Verify token validity against current files
            is_valid, reason = self.approval_service.verify_approval_validity(application_id, operator_token)
            if not is_valid:
                raise PermissionError(
                    f"SUBMISSION BLOCKED: Approval token for '{application_id}' is invalid or expired ({reason}). "
                    f"A fresh operator approval is required."
                )

            # In Phase 4, the system assists the candidate but stops at ready-to-submit
            return True, f"GATE PASSED: Application '{application_id}' is approved for manual submission."
        finally:
            conn.close()
