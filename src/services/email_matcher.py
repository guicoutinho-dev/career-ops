"""
Multi-signal matcher to correlate inbound emails with existing applications.
Signals evaluated:
1. Exact or Fuzzy Company Name in Subject, Body, or Sender Domain.
2. Job Title match.
3. Application/Job Reference IDs (e.g. SEEK / Workday req IDs).
"""

import re
from typing import Optional, Tuple, Dict, Any, List
import sqlite3
from src.database.connection import get_connection


class EmailMatcher:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path

    def get_tracked_applications(self, conn: sqlite3.Connection) -> List[Dict[str, Any]]:
        query = """
            SELECT a.id, j.employer, j.title, j.description, a.status
            FROM applications a
            JOIN jobs j ON a.job_id = j.id
        """
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        apps = []
        for r in rows:
            apps.append({
                "id": r[0],
                "employer": r[1],
                "job_title": r[2],
                "description": r[3] or "",
                "status": r[4]
            })
        return apps

    def match(
        self,
        sender: str,
        subject: str,
        body: str,
        conn: Optional[sqlite3.Connection] = None
    ) -> Tuple[Optional[str], float, List[str]]:
        """
        Attempts to match email against known applications.
        Returns: (matched_application_id, confidence, signals_list)
        """
        should_close = False
        if conn is None:
            conn = get_connection(self.db_path) if self.db_path else get_connection()
            should_close = True

        try:
            apps = self.get_tracked_applications(conn)
            best_match_id = None
            best_confidence = 0.0
            best_signals = []

            # Strip recommendations footer from email body (e.g. SEEK / Indeed "jobs you might like")
            clean_body = body
            for sep in ["explore other roles", "jobs you might be interested in", "similar jobs", "recommended jobs", "people also viewed"]:
                if sep in clean_body.lower():
                    clean_body = clean_body[:clean_body.lower().index(sep)]

            search_space = f"{sender}\n{subject}\n{clean_body}".lower()
            subject_lower = subject.lower()

            GENERIC_COMPANY_WORDS = {
                "services", "service", "pty", "ltd", "limited", "group", "australia",
                "recruitment", "consulting", "solutions", "international", "management",
                "holdings", "company", "enterprises", "corporation", "corp", "associates"
            }

            for app in apps:
                emp = (app["employer"] or "").strip().lower()
                title = (app["job_title"] or "").strip().lower()
                signals = []
                confidence = 0.0

                # 1. Company Name Signal (Strong)
                if emp and len(emp) >= 3:
                    clean_emp = re.escape(emp)
                    # Priority match in Subject
                    if re.search(rf"\b{clean_emp}\b", subject_lower):
                        signals.append(f"company_in_subject:{app['employer']}")
                        confidence += 0.85
                    elif re.search(rf"\b{clean_emp}\b", search_space):
                        signals.append(f"company_name_match:{app['employer']}")
                        confidence += 0.60
                    else:
                        emp_tokens = [w for w in re.findall(r"\w+", emp) if len(w) > 3 and w not in GENERIC_COMPANY_WORDS]
                        if emp_tokens and all(token in subject_lower for token in emp_tokens):
                            signals.append(f"company_tokens_in_subject:{emp_tokens}")
                            confidence += 0.70
                        elif emp_tokens and all(token in search_space for token in emp_tokens):
                            signals.append(f"company_tokens_match:{emp_tokens}")
                            confidence += 0.50
                        elif emp_tokens and any(token in search_space for token in emp_tokens):
                            matched_tokens = [t for t in emp_tokens if t in search_space]
                            signals.append(f"partial_company_match:{matched_tokens}")
                            confidence += 0.30

                # 2. Sender Domain Match
                if emp and "@" in sender:
                    domain_part = sender.split("@")[-1].lower()
                    domain_root = domain_part.split(".")[0]
                    clean_emp_alphanumeric = re.sub(r"[^a-z0-9]", "", emp)
                    if domain_root not in GENERIC_COMPANY_WORDS:
                        if domain_root in clean_emp_alphanumeric or (clean_emp_alphanumeric and clean_emp_alphanumeric in domain_part):
                            signals.append(f"sender_domain_match:{domain_part}")
                            confidence += 0.25

                # 3. Title Match
                if title:
                    role_stopwords = {"junior", "lead", "senior", "entry", "level", "and", "for", "with"}
                    role_words = [w for w in re.findall(r"\w+", title) if len(w) > 2 and w not in role_stopwords]
                    matched_in_subject = [w for w in role_words if w in subject_lower]
                    matched_in_body = [w for w in role_words if w in search_space]

                    if len(matched_in_subject) >= 2 or (len(role_words) == 1 and matched_in_subject):
                        signals.append(f"title_in_subject:{matched_in_subject}")
                        confidence += 0.40
                    elif len(matched_in_body) >= 2 or (len(role_words) == 1 and matched_in_body):
                        signals.append(f"title_keywords_match:{matched_in_body}")
                        confidence += 0.25

                if confidence > best_confidence:
                    best_confidence = confidence
                    best_match_id = app["id"]
                    best_signals = signals

            if best_confidence >= 0.50:
                return best_match_id, min(best_confidence, 0.99), best_signals
            
            return None, 0.0, []

        finally:
            if should_close and conn:
                conn.close()
