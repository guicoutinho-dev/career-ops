import hashlib
import datetime
import hmac
import json
import re
import sys
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
from src.database.connection import get_connection
from src.models.approval import ApprovalPackage
from src.services.event_logger import EventLogger


class ApprovalService:
    """Manages immutable review packages, cryptographic hashes, and manual approval gates."""

    ANSWER_FIELDS = {
        "first_name", "last_name", "email", "phone", "linkedin", "work_rights",
        "sponsorship_required", "availability", "salary_expectation", "relocation",
    }
    OPTIONAL_ANSWER_FIELDS = {"last_name", "phone", "linkedin", "availability", "salary_expectation", "relocation"}
    REQUIRED_ANSWER_FIELDS = ANSWER_FIELDS - OPTIONAL_ANSWER_FIELDS
    ELIGIBLE_STATUSES = {
        "Evaluated", "QUALIFIED", "READY_FOR_REVIEW", "ready_for_review",
        "PENDING_APPROVAL", "NEEDS_REVIEW",
    }

    def __init__(self, db_path: Path | str = "data/applications.db", applications_dir: Path | str = "applications"):
        self.db_path = db_path
        self.applications_dir = Path(applications_dir)

    def compute_file_hash(self, file_path: Path | str) -> str:
        """Computes SHA256 of file content, or empty hash if file does not exist."""
        p = Path(file_path)
        if not p.exists():
            return hashlib.sha256(b"").hexdigest()
        return hashlib.sha256(p.read_bytes()).hexdigest()

    def resolve_application_dir(self, application_id: str) -> Path:
        """Resolve only a safe, direct-child application directory."""
        if not isinstance(application_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", application_id):
            raise ValueError("Invalid application ID for filesystem access.")
        root = self.applications_dir.resolve()
        resolved = (root / application_id).resolve()
        if resolved.parent != root:
            raise ValueError("Application directory must remain inside the applications root.")
        return resolved

    def build_package(self, application_id: str) -> Optional[ApprovalPackage]:
        """Assembles an immutable snapshot of the application package."""
        package_dir = self.resolve_application_dir(application_id)
        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT a.id, a.match_score, a.status, j.id as job_id, j.employer, j.title, j.canonical_url, j.content_hash as job_desc_hash
                FROM applications a
                JOIN jobs j ON a.job_id = j.id
                WHERE a.id = ?;
            """, (application_id,))
            row = cursor.fetchone()
            if not row:
                return None

            cv_files = sorted(package_dir.glob("cv-*.pdf"))
            cover_files = sorted(package_dir.glob("cover-letter-*.pdf"))
            answers_path = package_dir / "answers.json"
            if len(cv_files) != 1 or len(cover_files) != 1 or not answers_path.is_file():
                return None
            if (
                cv_files[0].is_symlink()
                or cover_files[0].is_symlink()
                or answers_path.is_symlink()
                or cv_files[0].resolve().parent != package_dir
                or cover_files[0].resolve().parent != package_dir
                or answers_path.resolve().parent != package_dir
                or not cv_files[0].is_file()
                or not cover_files[0].is_file()
                or cv_files[0].stat().st_size == 0
                or cover_files[0].stat().st_size == 0
            ):
                return None
            try:
                answers = json.loads(answers_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
            if not isinstance(answers, dict) or answers.get("application_id") != application_id:
                return None
            answer_fields = answers.get("answers_by_field")
            if not isinstance(answer_fields, dict) or not self.ANSWER_FIELDS.issubset(answer_fields):
                return None
            if any(not isinstance(answer_fields[key], str) for key in self.ANSWER_FIELDS):
                return None
            if any(not answer_fields[key].strip() for key in self.REQUIRED_ANSWER_FIELDS):
                return None
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", answer_fields["email"]):
                return None

            return ApprovalPackage(
                application_id=row["id"],
                employer=row["employer"],
                job_title=row["title"],
                canonical_url=row["canonical_url"],
                job_description_hash=row["job_desc_hash"],
                match_score=float(row["match_score"] or 0.0),
                cv_filename=cv_files[0].name,
                cv_hash=self.compute_file_hash(cv_files[0]),
                cover_letter_filename=cover_files[0].name,
                cover_letter_hash=self.compute_file_hash(cover_files[0]),
                application_answers=answers,
                work_auth_declarations=[
                    str(answers[key]) for key in ("work_authorization_status", "requires_sponsorship")
                    if answers.get(key)
                ],
            )
        finally:
            conn.close()

    def compute_package_hash(self, pkg: ApprovalPackage) -> str:
        """Computes deterministic package hash."""
        seed = json.dumps(pkg.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()

    def display_review_package(self, pkg: ApprovalPackage, package_hash: str) -> bool:
        """Prints the exact package and artifact paths that the operator must review."""
        if not self._has_interactive_review_channel():
            return False
        package_dir = self.resolve_application_dir(pkg.application_id)
        print("Terminal output contains personal application answers; avoid transcript or session logging.")
        print(f"Review package: {pkg.employer} — {pkg.job_title}")
        print(f"URL: {pkg.canonical_url}")
        print(f"Match score: {pkg.match_score}")
        print(f"CV: {package_dir / pkg.cv_filename}")
        print(f"CV SHA-256: {pkg.cv_hash}")
        print(f"Cover letter: {package_dir / pkg.cover_letter_filename}")
        print(f"Cover letter SHA-256: {pkg.cover_letter_hash}")
        print("Application answers:")
        print(json.dumps(pkg.application_answers, indent=2, ensure_ascii=False))
        print(f"Package SHA-256: {package_hash}")
        return True

    @staticmethod
    def _has_interactive_review_channel() -> bool:
        """Require direct terminal input/output; never send candidate data to captured streams."""
        return sys.stdin.isatty() and sys.stdout.isatty()

    def issue_approval(
        self,
        application_id: str,
        confirmation_hash: Optional[str] = None,
        operator_name: str = "human:operator",
    ) -> Tuple[bool, str]:
        """Issues approval token and records approval in SQLite."""
        if not self._has_interactive_review_channel():
            return False, "INTERACTIVE_TERMINAL_REQUIRED"
        pkg = self.build_package(application_id)
        if not pkg:
            return False, "REVIEW_PACKAGE_INCOMPLETE"

        package_hash = self.compute_package_hash(pkg)
        self.display_review_package(pkg, package_hash)
        if not confirmation_hash or not hmac.compare_digest(confirmation_hash, package_hash):
            return False, "EXPLICIT_PACKAGE_CONFIRMATION_REQUIRED"
        expected_confirmation = f"APPROVE {application_id} {package_hash}"
        try:
            operator_confirmation = input(
                f"After reviewing all artifacts, type exactly '{expected_confirmation}': "
            )
        except EOFError:
            return False, "EXPLICIT_OPERATOR_CONFIRMATION_REQUIRED"
        if not hmac.compare_digest(operator_confirmation, expected_confirmation):
            return False, "EXPLICIT_OPERATOR_CONFIRMATION_REQUIRED"
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        approval_id = f"appr_{application_id}_{package_hash[:8]}"

        conn = get_connection(self.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE;")
            cursor = conn.cursor()
            logger = EventLogger(conn)

            cursor.execute("SELECT status FROM applications WHERE id = ?;", (application_id,))
            status_row = cursor.fetchone()
            if not status_row or status_row["status"] not in self.ELIGIBLE_STATUSES:
                conn.rollback()
                return False, "APPLICATION_STATUS_NOT_ELIGIBLE"

            # Insert approval
            cursor.execute("""
                INSERT OR REPLACE INTO approvals (id, application_id, package_hash, decision, approved_by, approved_at, invalidated_at)
                VALUES (?, ?, ?, 'APPROVED', ?, ?, NULL);
            """, (approval_id, application_id, package_hash, operator_name, now_iso))

            # Transition application to APPROVED
            cursor.execute("""
                UPDATE applications
                SET status = 'APPROVED', updated_at = ?
                WHERE id = ? AND status IN ({placeholders});
            """.format(placeholders=", ".join("?" for _ in self.ELIGIBLE_STATUSES)),
                (now_iso, application_id, *sorted(self.ELIGIBLE_STATUSES)))
            if cursor.rowcount != 1:
                conn.rollback()
                return False, "APPLICATION_STATUS_NOT_ELIGIBLE"

            current_pkg = self.build_package(application_id)
            if not current_pkg or not hmac.compare_digest(self.compute_package_hash(current_pkg), package_hash):
                conn.rollback()
                return False, "PACKAGE_CHANGED_DURING_APPROVAL"

            logger.log_event(
                entity_type="application",
                entity_id=application_id,
                event_type="APPROVAL_ISSUED",
                actor=operator_name,
                correlation_id=approval_id,
                payload={"package_hash": package_hash, "approved_at": now_iso},
                commit=False,
            )
            conn.commit()
            return True, package_hash
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def verify_approval_validity(self, application_id: str, operator_token: Optional[str] = None) -> Tuple[bool, str]:
        """
        Verifies if an existing approval is still valid.
        Returns (is_valid, reason).
        If document hash or job description has changed, invalidates approval.
        """
        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM approvals
                WHERE application_id = ? AND decision = 'APPROVED' AND invalidated_at IS NULL
                ORDER BY approved_at DESC LIMIT 1;
            """, (application_id,))
            row = cursor.fetchone()
            if not row:
                return False, "NO_ACTIVE_APPROVAL"

            approved_hash = row["package_hash"]
            if not operator_token or not hmac.compare_digest(operator_token, approved_hash):
                return False, "OPERATOR_TOKEN_REQUIRED_OR_MISMATCH"

            # Rebuild current package
            current_pkg = self.build_package(application_id)
            if not current_pkg:
                return False, "APPLICATION_NOT_FOUND"

            current_hash = self.compute_package_hash(current_pkg)
            if current_hash != approved_hash:
                # Invalidate!
                now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
                cursor.execute("""
                    UPDATE approvals
                    SET invalidated_at = ?, decision = 'REJECTED'
                    WHERE id = ?;
                """, (now_iso, row["id"]))

                cursor.execute("""
                    UPDATE applications
                    SET status = 'NEEDS_REVIEW', updated_at = ?
                    WHERE id = ?;
                """, (now_iso, application_id))

                conn.commit()
                return False, "PACKAGE_HASH_MISMATCH_INVALIDATED"

            return True, "VALID"
        finally:
            conn.close()
