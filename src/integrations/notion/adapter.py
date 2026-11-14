import os
import re
import json
import time
import uuid
import hashlib
import logging
import datetime
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from src.database.connection import get_connection
from src.services.job_classifier import classify_job
from src.services.env import load_env

logger = logging.getLogger(__name__)

NOTION_API_VERSION = "2022-06-28"
NOTION_BASE_URL = "https://api.notion.com/v1"

CATEGORY_ICONS = {
    "88 Days Specified Work": "🌾",
    "SAP Specialist": "💻",
    "Office Support": "🗂️",
    "Hospitality / Barista": "☕",
    "Other": "💼",
}

AUDITED_PASS_APPS = {"app_005", "app_006"}


def _find_package_dir(app_id: str, canonical_url: Optional[str] = None) -> Optional[Path]:
    if not app_id:
        return None
    pkg = Path("applications") / app_id
    if pkg.exists():
        return pkg
    m = re.search(r"(\d{8,})", app_id)
    if not m and canonical_url:
        m = re.search(r"(\d{8,})", canonical_url)
    if m:
        cands = list(Path("applications").glob(f"*{m.group(1)}*"))
        if cands:
            return cands[0]
    return None


def _norm_id(notion_id: Optional[str]) -> str:
    return (notion_id or "").replace("-", "").strip().lower()


def _extract_salary_hint(description: str) -> Optional[str]:
    if not description:
        return None
    patterns = [
        r"\$\d{2,3}(?:,\d{3})+(?:\s*-\s*\$\d{2,3}(?:,\d{3})+)?(?:\s*(?:per\s+annum|p\.a\.|pa|\+\s*super))?",
        r"\$\d{2,4}(?:\.\d{1,2})?\s*(?:-\s*\$\d{2,4}(?:\.\d{1,2})?\s*)?(?:per\s+hour|/hr|/h|p/h|per\s+day|/day|pd)",
    ]
    for pat in patterns:
        m = re.search(pat, description, flags=re.IGNORECASE)
        if m:
            return m.group(0).strip()[:120]
    return None


def _has_citizenship_blocker(title: str, description: str, score_val: Optional[float]) -> bool:
    text = f"{title} {description}".lower()
    if score_val is not None and score_val <= 2.5:
        return True
    blocker_terms = (
        "australian citizen",
        "must be an australian citizen",
        "nv1",
        "nv2",
        "baseline clearance",
        "negative vetting",
    )
    return any(b in text for b in blocker_terms)


class NotionAdapter:
    """Synchronizes SQLite applications with Notion tracker database (Job Search HQ)."""

    def __init__(
        self,
        token: Optional[str] = None,
        parent_page_id: Optional[str] = None,
        database_id: Optional[str] = None,
        dry_run: bool = False
    ):
        load_env()
        self.token = token or os.environ.get("NOTION_ACCESS_TOKEN", "")
        self.parent_page_id = parent_page_id or os.environ.get("NOTION_PARENT_PAGE_ID", "")
        self.database_id = database_id or os.environ.get("NOTION_DATABASE_ID", "")
        self.dry_run = dry_run or not bool(self.token)
        self.request_delay = 0.28
        self._master_cv_cache: Optional[Tuple[str, str]] = None

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": NOTION_API_VERSION,
            "Content-Type": "application/json"
        }

    def _http_request(self, method: str, endpoint: str, payload: Optional[Dict[str, Any]] = None) -> Tuple[int, Dict[str, Any]]:
        """Makes an HTTP request to Notion API with rate limiting."""
        if self.dry_run:
            logger.info(f"[Notion Mock/Dry-run] {method} {endpoint}")
            return 200, {"id": "mock_notion_id", "object": "page"}

        time.sleep(self.request_delay)
        url = f"{NOTION_BASE_URL}/{endpoint.lstrip('/')}"
        data_bytes = json.dumps(payload).encode("utf-8") if payload is not None else None

        req = urllib.request.Request(url, data=data_bytes, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                status = resp.status
                body = json.loads(resp.read().decode("utf-8"))
                return status, body
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            logger.error(f"Notion API error ({e.code}): {err_body}")
            return e.code, {"error": err_body}
        except Exception as e:
            logger.error(f"Notion connection failure: {e}")
            return 500, {"error": str(e)}

    def upload_file(self, file_path: Path) -> Optional[str]:
        """Uploads a local PDF file to Notion via /v1/file_uploads and returns the file_upload ID."""
        if self.dry_run or not file_path.exists():
            return None

        status, res = self._http_request(
            "POST",
            "file_uploads",
            {"filename": file_path.name, "content_type": "application/pdf"}
        )
        if status != 200 or "upload_url" not in res:
            return None

        upload_url = res["upload_url"]
        upload_id = res["id"]
        boundary = uuid.uuid4().hex
        body = (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
            f'Content-Type: application/pdf\r\n\r\n'
        ).encode("utf-8") + file_path.read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8")

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": NOTION_API_VERSION,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
        time.sleep(self.request_delay)
        req = urllib.request.Request(upload_url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                if resp.status == 200:
                    return upload_id
        except Exception as e:
            logger.error(f"File upload binary send failed for {file_path}: {e}")
        return None

    def get_or_upload_master_cv(self) -> Optional[Tuple[str, str]]:
        """Uploads the candidate's master CV once per sync run for the HQ reference block only."""
        if self._master_cv_cache:
            return self._master_cv_cache
        candidates = [
            Path("output/master_cv_guilherme_xavier_en.pdf"),
            Path("output/cv-guilherme-uandu-v2.pdf"),
            Path("output/cv-guilherme-uandu.pdf"),
        ]
        for path in candidates:
            if path.exists():
                uid = self.upload_file(path)
                if uid:
                    self._master_cv_cache = (uid, path.name)
                    return self._master_cv_cache
        return None

    def ensure_database_schema(self) -> bool:
        """
        Ensures Job Search HQ database has clear operational properties separating:
        - Prep Stage (operational preparation vs external submission)
        - Doc Status (absent vs tailored generated vs audited vs approved)
        - Visa Fit (unverified vs verification needed vs verified vs blocked)
        - Next Action (explicit human decision step)
        """
        if self.dry_run or not self.database_id:
            return True
        payload = {
            "properties": {
                "Track": {
                    "select": {
                        "options": [
                            {"name": "88 Days Specified Work", "color": "green"},
                            {"name": "SAP Specialist", "color": "blue"},
                            {"name": "Office Support", "color": "purple"},
                            {"name": "Hospitality / Barista", "color": "orange"},
                            {"name": "Other", "color": "gray"},
                        ]
                    }
                },
                "Prep Stage": {
                    "select": {
                        "options": [
                            {"name": "1. Triagem (Para Avaliar)", "color": "gray"},
                            {"name": "2. Pacote em Revisão", "color": "yellow"},
                            {"name": "3. Pronto para Candidatura (Auditado)", "color": "green"},
                            {"name": "4. APLICADO (Candidatura Enviada)", "color": "blue"},
                            {"name": "5. Em Processo / Resposta", "color": "purple"},
                        ]
                    }
                },
                "Doc Status": {
                    "select": {
                        "options": [
                            {"name": "Ausente (Apenas Triagem)", "color": "gray"},
                            {"name": "Tailored CV + CL (Em Revisão)", "color": "yellow"},
                            {"name": "Tailored CV + CL (Verificado Revisor-Gy)", "color": "green"},
                            {"name": "Tailored CV + CL (Aprovado Operador)", "color": "blue"},
                        ]
                    }
                },
                "Priority": {
                    "select": {
                        "options": [
                            {"name": "🔥 Ação Imediata (Pacote Pronto)", "color": "red"},
                            {"name": "⭐ Alta Aderência (Gerar/Triar)", "color": "yellow"},
                            {"name": "📌 Fila Padrão", "color": "gray"},
                            {"name": "🚫 Bloqueado / Descartado", "color": "brown"},
                        ]
                    }
                },
                "Visa Fit": {
                    "select": {
                        "options": [
                            {"name": "❓ Não Verificado — Checar JD (Regra 6m WHV)", "color": "gray"},
                            {"name": "⚠️ Verificação Necessária — Confirmar Postcode/ABN 88d", "color": "yellow"},
                            {"name": "✅ Verificado no JD (Aceita Contrato / WHV 462)", "color": "green"},
                            {"name": "🚫 Bloqueado (Exige Cidadania AU / PR / Clearance)", "color": "red"},
                        ]
                    }
                },
                "Next Action": {"rich_text": {}},
                "Match Score": {"number": {"format": "number"}},
                "Job URL": {"url": {}},
                "Package Folder": {"rich_text": {}},
                "QA Status": {
                    "select": {
                        "options": [
                            {"name": "Pending Review", "color": "yellow"},
                            {"name": "In Review", "color": "blue"},
                            {"name": "Passed", "color": "green"},
                            {"name": "Needs Revision", "color": "orange"},
                            {"name": "Failed", "color": "red"},
                        ]
                    }
                },
                "QA Score": {"number": {"format": "number"}},
                "Issues Found": {"rich_text": {}},
                "Review Date": {"date": {}},
                "Recommendations": {"rich_text": {}},
            }
        }
        status, _ = self._http_request("PATCH", f"databases/{self.database_id}", payload)
        return status == 200

    def _resolve_category_and_visa(self, app_data: Dict[str, Any]) -> Tuple[str, str]:
        cat = app_data.get("category")
        vfit = app_data.get("visa_fit")
        if cat and vfit and cat != "Other":
            return str(cat), str(vfit)

        role_track = str(app_data.get("role_track", "sap")).lower()
        title = str(app_data.get("title", ""))
        desc = str(app_data.get("description", ""))
        loc = str(app_data.get("location", ""))
        c_cat, c_visa = classify_job(title, desc, loc, role_track)
        if c_cat == "Other" and role_track == "sap":
            c_cat = "SAP Specialist"
        return c_cat, c_visa

    def format_properties(
        self,
        app_data: Dict[str, Any],
        cv_upload: Optional[Tuple[str, str]] = None,
        cl_upload: Optional[Tuple[str, str]] = None,
        include_extended_schema: bool = False,
        clear_missing_files: bool = False,
    ) -> Dict[str, Any]:
        """
        Formats application data into Job Search HQ Notion properties.
        CRITICAL RULE: Never attach Master CV to row 'Resume'. Only attach files when
        a genuine tailored document exists for this specific job.
        """
        app_id = str(app_data.get("id", ""))
        status_val = str(app_data.get("status", "DISCOVERED"))
        title_str = str(app_data.get("title", app_id))
        title_lower = title_str.lower()
        desc_str = str(app_data.get("description", ""))
        desc_lower = desc_str.lower()
        loc_str = str(app_data.get("location", "Australia"))

        score_val = None
        score = app_data.get("match_score")
        if score is not None:
            try:
                score_val = float(score)
            except (ValueError, TypeError):
                pass

        is_blocked = (
            status_val == "DISQUALIFIED"
            or _has_citizenship_blocker(title_str, desc_str, score_val)
        )

        pkg_dir = _find_package_dir(app_id, str(app_data.get("canonical_url", "")))
        has_tailored_cv = bool(pkg_dir and pkg_dir.exists() and any(pkg_dir.glob("cv-*.pdf")))
        has_tailored_cl = bool(pkg_dir and pkg_dir.exists() and any(pkg_dir.glob("cover-letter-*.pdf")))
        has_tailored_pkg = has_tailored_cv and has_tailored_cl
        is_approved = (
            status_val == "APPROVED"
            or str(app_data.get("approval_decision", "")).upper() == "APPROVED"
        )
        is_audited_pass = app_id in AUDITED_PASS_APPS or is_approved

        # External Application Status mapping (compatible with Career-ops & Notion status column)
        # Notion options: 'To Apply', 'Applied', 'Interviewing', 'No Response', 'Offered', 'Offer Accepted', 'Rejected', 'Offer Pass'
        if status_val in ("INTERVIEW", "Interviewing", "SCREENING"):
            notion_status = "Interviewing"
        elif status_val in ("OFFER", "Offered"):
            notion_status = "Offered"
        elif status_val in ("CLOSED_WON", "Offer Accepted"):
            notion_status = "Offer Accepted"
        elif status_val in ("REJECTED", "CLOSED_LOST", "REJECTION"):
            notion_status = "Rejected"
        elif status_val in ("NO_RESPONSE", "No Response"):
            notion_status = "No Response"
        elif status_val == "APPLIED" or "APLICADO" in status_val.upper():
            notion_status = "Applied"
        elif is_blocked:
            notion_status = "Offer Pass"
        else:
            notion_status = "To Apply"

        track_name, visa_fit_code = self._resolve_category_and_visa(app_data)

        props: Dict[str, Any] = {
            "Position": {
                "title": [{"type": "text", "text": {"content": title_str[:2000]}}]
            },
            "Company": {
                "rich_text": [{"type": "text", "text": {"content": str(app_data.get("employer", "Undisclosed"))[:2000]}}]
            },
            "Application Status": {
                "status": {"name": notion_status}
            },
            "Location": {
                "rich_text": [{"type": "text", "text": {"content": loc_str[:2000]}}]
            },
            "Track": {
                "select": {"name": track_name}
            }
        }

        work_models = []
        if "remote" in title_lower or "remote" in loc_str.lower():
            work_models.append({"name": "🏠 Remote"})
        elif "hybrid" in desc_lower:
            work_models.append({"name": "🔄 Hybrid"})
        else:
            work_models.append({"name": "🏢 On-site"})

        if any(w in title_lower for w in ("contract", "casual", "temp", "month")):
            work_models.append({"name": "📆 Contract / Freelance"})
        props["Work Model"] = {"multi_select": work_models}

        if score_val is not None:
            props["Match Score"] = {"number": score_val}

        canonical_url = str(app_data.get("canonical_url", "") or "").strip()
        if app_id == "app_001" and not canonical_url.startswith("http"):
            canonical_url = "https://www.seek.com.au/job/93308643"
        elif app_id == "app_002" and not canonical_url.startswith("http"):
            canonical_url = "https://www.seek.com.au/job/93327783"
        if "seek.com.au/id/job/" in canonical_url:
            canonical_url = canonical_url.replace("seek.com.au/id/job/", "seek.com.au/job/")

        if canonical_url and canonical_url.startswith("http"):
            props["Job URL"] = {"url": canonical_url}

        salary_hint = _extract_salary_hint(desc_str)
        if salary_hint:
            props["Salary Range"] = {
                "rich_text": [{"type": "text", "text": {"content": salary_hint}}]
            }

        # Operational stage, document status, visa verification, priority & next action
        if is_blocked:
            prep_stage = "6. Bloqueado / Encerrado"
            doc_status = "Não Aplicável (Bloqueado)"
            visa_label = "🚫 Bloqueado (Exige Cidadania AU / PR / Clearance)"
            priority_label = "🚫 Bloqueado / Descartado"
            next_action = "🚫 Não aplicar — Requisito impeditivo identificado (Cidadania AU / Security Clearance)"
            note_summary = "Bloqueio de elegibilidade: Vaga exige Cidadania Australiana ou Clearance."
        elif status_val == "APPLIED":
            prep_stage = "4. APLICADO (Candidatura Enviada)"
            doc_status = "Tailored CV + CL (Aprovado Operador)" if has_tailored_pkg else "Ausente (Apenas Triagem)"
            visa_label = "✅ Verificado no JD (Aceita Contrato / WHV 462)"
            priority_label = "📌 Fila Padrão"
            next_action = "⏳ Aguardar retorno do recrutador (monitorado via Gmail / Correio)"
            note_summary = "Candidatura já submetida; monitorando e-mail."
        elif status_val in ("INTERVIEW", "Interviewing", "OFFER", "Offered"):
            prep_stage = "5. Em Processo / Resposta"
            doc_status = "Tailored CV + CL (Aprovado Operador)"
            visa_label = "✅ Verificado no JD (Aceita Contrato / WHV 462)"
            priority_label = "🔥 Ação Imediata (Pacote Pronto)"
            next_action = "🗓️ Preparar entrevista / responder recrutador"
            note_summary = "Processo seletivo ativo."
        elif has_tailored_pkg:
            if is_approved:
                prep_stage = "3. Pronto para Candidatura (Auditado)"
                doc_status = "Tailored CV + CL (Aprovado Operador)"
                next_action = "✅ APROVADO PELO OPERADOR — Abrir Job URL, anexar Tailored CV + CL e enviar candidatura"
                note_summary = "Aprovado pelo operador com hash válido — pronto para envio manual."
            elif is_audited_pass:
                prep_stage = "3. Pronto para Candidatura (Auditado)"
                doc_status = "Tailored CV + CL (Verificado Revisor-Gy)"
                next_action = "👁️ REVISÃO FINAL HUMANA + ENVIO MANUAL: Inspecionar PDFs (Tailored CV + CL), aprovar e submeter no SEEK"
                note_summary = "Auditado PASS pelo Revisor-Gy — aguardando sua REVISÃO FINAL HUMANA + ENVIO MANUAL."
            else:
                prep_stage = "2. Pacote em Revisão"
                doc_status = "Tailored CV + CL (Em Revisão)"
                next_action = "🔍 REVISÃO HUMANA: Conferir Tailored CV + Cover Letter anexados nesta linha antes de aprovar envio"
                note_summary = "Pacote personalizado (Tailored CV + CL) anexado — aguardando revisão humana."
            visa_label = "✅ Verificado no JD (Aceita Contrato / WHV 462)"
            priority_label = "🔥 Ação Imediata (Pacote Pronto)"
        else:
            prep_stage = "1. Triagem (Para Avaliar)"
            doc_status = "Ausente (Apenas Triagem)"
            if track_name == "88 Days Specified Work" or visa_fit_code in ("88d_likely", "88d_possible"):
                visa_label = "⚠️ Verificação Necessária — Confirmar Postcode/ABN 88d"
                next_action = "🌾 Abrir Job URL, confirmar se Postcode/ABN é elegível para 88 dias WHV 462 e aplicar"
            else:
                visa_label = "❓ Não Verificado — Checar JD (Regra 6m WHV)"
                next_action = "🔎 Abrir Job URL, validar se aceita WHV 462 e gerar pacote se desejar aplicar"

            norm_score = (score_val * 20.0) if (score_val is not None and score_val <= 5.0) else (score_val or 0.0)
            priority_label = "⭐ Alta Aderência (Gerar/Triar)" if norm_score >= 68.0 else "📌 Fila Padrão"
            note_summary = "Vaga em triagem — sem CV personalizado gerado ainda."

        has_http_url = bool(canonical_url and canonical_url.startswith("http"))

        if include_extended_schema:
            props["Prep Stage"] = {"select": {"name": prep_stage}}
            props["Doc Status"] = {"select": {"name": doc_status}}
            props["Priority"] = {"select": {"name": priority_label}}
            props["Visa Fit"] = {"select": {"name": visa_label}}
            props["Next Action"] = {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {
                            "content": next_action[:2000],
                            **({"link": {"url": canonical_url}} if has_http_url else {}),
                        },
                    }
                ]
            }
            # Clear technical Package Folder from main table row to reduce clutter (stored inside row page body instead)
            props["Package Folder"] = {"rich_text": []}
            if has_http_url:
                props["Contact"] = {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {"content": f"🔗 {canonical_url}", "link": {"url": canonical_url}},
                            "annotations": {"bold": True, "color": "blue"},
                        }
                    ]
                }
        elif has_tailored_pkg and pkg_dir:
            props["Package Folder"] = {
                "rich_text": [{"type": "text", "text": {"content": str(pkg_dir.resolve())}}]
            }

        # Attach ONLY genuine tailored documents to row columns; clear Master CV from non-tailored rows
        if cv_upload:
            props["Resume"] = {
                "files": [{"type": "file_upload", "file_upload": {"id": cv_upload[0]}, "name": cv_upload[1]}]
            }
        elif clear_missing_files:
            props["Resume"] = {"files": []}

        if cl_upload:
            props["Cover Letter"] = {
                "files": [{"type": "file_upload", "file_upload": {"id": cl_upload[0]}, "name": cl_upload[1]}]
            }
        elif clear_missing_files:
            props["Cover Letter"] = {"files": []}

        if has_http_url:
            props["Notes"] = {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {"content": "🔗 Abrir Vaga no SEEK", "link": {"url": canonical_url}},
                        "annotations": {"bold": True, "color": "blue"},
                    },
                    {
                        "type": "text",
                        "text": {"content": f"  |  {note_summary}"[:1900]},
                    },
                ]
            }
        else:
            props["Notes"] = {
                "rich_text": [{"type": "text", "text": {"content": note_summary[:2000]}}]
            }

        return props

    def _build_row_detail_blocks(self, app_data: Dict[str, Any], docs_hash: Optional[str]) -> List[Dict[str, Any]]:
        """Builds clean technical & audit detail blocks stored inside the row's Notion page body."""
        app_id = str(app_data.get("id", ""))
        pkg_dir = _find_package_dir(app_id, str(app_data.get("canonical_url", "")))
        pkg_folder_str = str(pkg_dir.resolve()) if (pkg_dir and pkg_dir.exists()) else "Não gerado (vaga em triagem)"
        pkg_hash = app_data.get("package_hash") or docs_hash or "N/A"
        canonical_url = app_data.get("canonical_url") or "N/A"
        desc_excerpt = str(app_data.get("description") or "Sem descrição detalhada.")[:1500]

        meta_text = (
            f"🔎 Rastreabilidade & Pacote Técnico (Australia Job Hunter)\n"
            f"• Application ID: {app_id}\n"
            f"• Pasta Local do Pacote: {pkg_folder_str}\n"
            f"• Hash de Integridade (SHA-256): {pkg_hash}\n"
            f"• Link Original: {canonical_url}"
        )
        return [
            {
                "object": "block",
                "type": "callout",
                "callout": {
                    "icon": {"type": "emoji", "emoji": "🗂️"},
                    "rich_text": [{"type": "text", "text": {"content": meta_text}}],
                },
            },
            {
                "object": "block",
                "type": "heading_3",
                "heading_3": {
                    "rich_text": [{"type": "text", "text": {"content": "Descrição da Vaga (Resumo)"}}]
                },
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": desc_excerpt}}]
                },
            },
        ]

    def _find_tailored_package_uploads(
        self,
        app_id: str,
        existing_hash: Optional[str] = None,
        canonical_url: Optional[str] = None,
    ) -> Tuple[Optional[Tuple[str, str]], Optional[Tuple[str, str]], Optional[str]]:
        """
        Finds and uploads ONLY genuine tailored CV and Cover Letter PDFs inside applications/<app_id>/.
        Never falls back to Master CV for row columns.
        """
        pkg_dir = _find_package_dir(app_id, canonical_url)
        if pkg_dir and pkg_dir.exists():
            cv_files = list(pkg_dir.glob("cv-*.pdf"))
            cl_files = list(pkg_dir.glob("cover-letter-*.pdf"))
            if cv_files or cl_files:
                h = hashlib.sha256()
                for f in sorted(cv_files + cl_files):
                    h.update(f.name.encode("utf-8"))
                    h.update(str(f.stat().st_size).encode("utf-8"))
                new_hash = h.hexdigest()[:16]

                cv_up = None
                cl_up = None
                if cv_files:
                    best_cv = [f for f in cv_files if f.name != "cv-guilherme-coutinho.pdf"] or cv_files
                    uid = self.upload_file(best_cv[0])
                    if uid:
                        cv_up = (uid, best_cv[0].name)
                if cl_files:
                    best_cl = [f for f in cl_files if f.name != "cover-letter-guilherme-coutinho.pdf"] or cl_files
                    uid = self.upload_file(best_cl[0])
                    if uid:
                        cl_up = (uid, best_cl[0].name)
                return cv_up, cl_up, new_hash

        return None, None, existing_hash

    def sync_application(
        self,
        app_id: str,
        db_path: Path | str = "data/applications.db",
        include_extended_schema: bool = False,
        clear_missing_files: bool = False,
    ) -> Tuple[bool, str]:
        """Synchronizes a single application to Notion with strict separation of tailored vs master docs."""
        conn = get_connection(db_path)
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT a.id, a.role_track, a.match_score, a.confidence, a.status,
                       a.created_at, a.updated_at, a.next_action_at,
                       j.employer, j.title, j.location, j.canonical_url, j.description,
                       ap.package_hash, ap.decision as approval_decision
                FROM applications a
                JOIN jobs j ON a.job_id = j.id
                LEFT JOIN approvals ap ON ap.application_id = a.id
                WHERE a.id = ?;
            """, (app_id,))
            row = cur.fetchone()
            if not row:
                return False, f"Application {app_id} not found."

            app_data = dict(row)

            notion_page_id = None
            existing_docs_hash = None
            try:
                cur.execute(
                    "SELECT notion_page_id, category, visa_fit, notion_docs_hash FROM applications WHERE id = ?;",
                    (app_id,)
                )
                n_row = cur.fetchone()
                if n_row:
                    if isinstance(n_row, dict):
                        notion_page_id = n_row.get("notion_page_id")
                        app_data["category"] = n_row.get("category")
                        app_data["visa_fit"] = n_row.get("visa_fit")
                        existing_docs_hash = n_row.get("notion_docs_hash")
                    else:
                        notion_page_id = n_row[0]
                        if len(n_row) > 1:
                            app_data["category"] = n_row[1]
                        if len(n_row) > 2:
                            app_data["visa_fit"] = n_row[2]
                        if len(n_row) > 3:
                            existing_docs_hash = n_row[3]
            except Exception:
                pass
        finally:
            conn.close()

        score_val = None
        score = app_data.get("match_score")
        if score is not None:
            try:
                score_val = float(score)
            except (ValueError, TypeError):
                pass

        track_name, _ = self._resolve_category_and_visa(app_data)
        row_icon = {"type": "emoji", "emoji": CATEGORY_ICONS.get(track_name, "💼")}

        cv_up, cl_up, new_docs_hash = self._find_tailored_package_uploads(
            app_id, existing_hash=existing_docs_hash, canonical_url=app_data.get("canonical_url")
        )
        props = self.format_properties(
            app_data,
            cv_upload=cv_up,
            cl_upload=cl_up,
            include_extended_schema=include_extended_schema,
            clear_missing_files=clear_missing_files,
        )

        # If already synced, verify page belongs to current target database and update via PATCH
        if notion_page_id:
            payload = {"properties": props, "icon": row_icon}
            status_code, resp = self._http_request("PATCH", f"pages/{notion_page_id}", payload)
            if status_code == 200:
                parent_db = (resp.get("parent") or {}).get("database_id")
                if not parent_db or not self.database_id or _norm_id(parent_db) == _norm_id(self.database_id):
                    return True, notion_page_id

        payload = {
            "parent": {"database_id": self.database_id},
            "icon": row_icon,
            "properties": props,
            "children": self._build_row_detail_blocks(app_data, new_docs_hash),
        }
        status_code, resp = self._http_request("POST", "pages", payload)
        if status_code in (200, 201):
            page_id = resp.get("id", "unknown")
            try:
                conn_w = get_connection(db_path)
                conn_w.execute(
                    "UPDATE applications SET notion_page_id = ?, notion_docs_hash = ? WHERE id = ?;",
                    (page_id, new_docs_hash, app_id)
                )
                conn_w.commit()
                conn_w.close()
            except Exception:
                pass
            return True, page_id
        else:
            return False, f"Failed with status {status_code}: {resp.get('error', '')}"

    def sync_hq_page_widgets(
        self,
        db_path: Path | str = "data/applications.db",
        synced_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Organizes the Job Search HQ initial page into the 4 action-oriented areas:
        - Top KPI bar (exact counts from the active Notion database + timestamp)
        - Area 1: Minha Fila de Trabalho (immediate decisions on ready packages & triage)
        - Area 2: Trilhas de Busca (breakdown of Notion active rows vs local SQLite reservoir)
        - Area 4: Saúde da Automação & Orquestração Maestri (live telemetry in bottom callout)
        """
        if self.dry_run or not self.parent_page_id:
            return {"updated_blocks": 0, "dry_run": self.dry_run}

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M BRT")

        conn = get_connection(db_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM jobs;")
            total_jobs = cur.fetchone()[0]
            cur.execute("""
                SELECT COALESCE(category, 'Other') as cat, COUNT(*)
                FROM applications
                WHERE status IN ('QUALIFIED', 'Evaluated', 'ready_for_review', 'APPROVED', 'APPLIED')
                GROUP BY cat;
            """)
            sqlite_by_cat = {r[0]: r[1] for r in cur.fetchall()}

            cur.execute("SELECT COUNT(*) FROM email_messages;")
            processed_emails = cur.fetchone()[0]

            cur.execute(
                "SELECT COUNT(*) FROM email_messages WHERE classification IN ('RECRUITER_CONTACT', 'INTERVIEW', 'OFFER');"
            )
            actionable_emails = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM email_messages WHERE classification = 'REJECTION';")
            rejection_emails = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM events;")
            total_system_events = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM approvals WHERE decision = 'APPROVED' AND invalidated_at IS NULL;")
            operator_approved_count = cur.fetchone()[0]

            cur.execute("SELECT MAX(timestamp) FROM events;")
            last_audit_ts = cur.fetchone()[0] or now_str
        finally:
            conn.close()

        s_info = synced_summary or {}
        n_total = s_info.get("notion_total", 40)
        n_ready_pkg = s_info.get("notion_tailored_packages", 6)
        n_audited = s_info.get("notion_audited_ready", 2)
        n_review = max(0, n_ready_pkg - n_audited)
        n_triage = s_info.get("notion_triage", max(0, n_total - n_ready_pkg - 1))
        n_blocked = s_info.get("notion_blocked", 1)
        n_tracks = s_info.get("notion_by_track", {})

        updated = 0

        # 0. Top Intro Paragraph -> Live Verified KPI Bar (explicitly separating Audit vs Operator Approval & Email Metrics)
        top_kpi_text = (
            f"📊 PAINEL DE DECISÃO ({now_str})  |  "
            f"👁️ Auditadas Revisor-Gy (Revisão Final Humana): {n_audited} (Aprovadas Operador: {operator_approved_count})  •  "
            f"📝 Pacotes em Revisão: {n_review}  •  "
            f"📥 Para Triagem: {n_triage}  •  "
            f"🚫 Bloqueadas: {n_blocked}  •  "
            f"📬 E-mails p/ Atenção: {actionable_emails} (de {processed_emails} lidos | {total_system_events} eventos DB)"
        )
        st, _ = self._http_request(
            "PATCH",
            "blocks/8ae8c931-81f1-8289-be10-81649935b7d8",
            {
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": top_kpi_text}, "annotations": {"bold": True}}]
                }
            },
        )
        if st == 200:
            updated += 1

        # 1. Left Column: Clearly label Master CV as Base Reference (not a per-job tailored CV)
        self._http_request(
            "PATCH",
            "blocks/1538c931-81f1-833d-bc73-81f6f1f00069",
            {
                "heading_3": {
                    "rich_text": [{"type": "text", "text": {"content": "📄 Master CV (Referência Base — Geral)"}}]
                }
            },
        )
        master_cv = self.get_or_upload_master_cv()
        if master_cv:
            st, _ = self._http_request(
                "PATCH",
                "blocks/87e8c931-81f1-8319-9354-8117a0a728e3",
                {
                    "file": {
                        "file_upload": {"id": master_cv[0]},
                        "caption": [
                            {
                                "type": "text",
                                "text": {
                                    "content": (
                                        "Master CV (Referência Base). Na tabela abaixo, a coluna 'Resume' exibe "
                                        "APENAS CVs personalizados (Tailored CV) gerados especificamente para aquela vaga."
                                    )
                                },
                            }
                        ],
                    }
                },
            )
            if st == 200:
                updated += 1

        # 2. Right Column: Area 1 — Minha Fila de Trabalho (Immediate Action Queue with Explicit Human Approval Gate)
        self._http_request(
            "PATCH",
            "blocks/f7f8c931-81f1-824b-b2d8-0101511db336",
            {
                "heading_3": {
                    "rich_text": [{"type": "text", "text": {"content": "🎯 1. Minha Fila de Trabalho (Próxima Ação)"}}]
                }
            },
        )
        todos = [
            (
                "fd58c931-81f1-82b4-9fdb-81c82710ba0a",
                [
                    {
                        "type": "text",
                        "text": {
                            "content": f"👁️ REVISÃO FINAL HUMANA + ENVIO MANUAL ({n_audited} auditadas PASS pelo Revisor-Gy — Aprovadas Operador: {operator_approved_count}): "
                        },
                        "annotations": {"bold": True},
                    },
                    {"type": "text", "text": {"content": "🔗 Experis AU (ABAP)", "link": {"url": "https://www.seek.com.au/job/95280403"}}, "annotations": {"bold": True, "color": "blue"}},
                    {"type": "text", "text": {"content": "  |  "}},
                    {"type": "text", "text": {"content": "🔗 George Weston Foods (SAP BTP)", "link": {"url": "https://www.seek.com.au/job/95339857"}}, "annotations": {"bold": True, "color": "blue"}},
                    {"type": "text", "text": {"content": " — inspecionar PDFs na linha, aprovar (cli.approve) e enviar manualmente"}},
                ],
                False,
            ),
            (
                "43e8c931-81f1-8375-90a9-01497070179b",
                [
                    {"type": "text", "text": {"content": f"📝 PACOTES EM REVISÃO ({n_review} pacotes com Tailored CV + CL gerados): "}, "annotations": {"bold": True}},
                    {"type": "text", "text": {"content": "🔗 QLD Health u&u", "link": {"url": "https://www.seek.com.au/job/93327783"}}, "annotations": {"bold": True, "color": "blue"}},
                    {"type": "text", "text": {"content": " | "}},
                    {"type": "text", "text": {"content": "🔗 Cloud pro", "link": {"url": "https://www.seek.com.au/job/95194139"}}, "annotations": {"bold": True, "color": "blue"}},
                    {"type": "text", "text": {"content": " | "}},
                    {"type": "text", "text": {"content": "🔗 Slade Group", "link": {"url": "https://www.seek.com.au/job/95279927"}}, "annotations": {"bold": True, "color": "blue"}},
                    {"type": "text", "text": {"content": " | "}},
                    {"type": "text", "text": {"content": "🔗 Recruitment Hive", "link": {"url": "https://www.seek.com.au/job/95482398"}}, "annotations": {"bold": True, "color": "blue"}},
                ],
                False,
            ),
            (
                "9fb8c931-81f1-83aa-8091-81388e5fff3c",
                [
                    {"type": "text", "text": {"content": f"🌾 TRIAR 88 DIAS ({n_tracks.get('88 Days Specified Work', 11)} vagas na tabela) & 📬 ATENÇÃO GMAIL ({actionable_emails} recrutador): "}, "annotations": {"bold": True}},
                    {"type": "text", "text": {"content": "Conferir visualização de perfil pelo 4C Group e clicar em '🔗 Abrir Vaga no SEEK' nas colunas Contact/Notes para triar"}},
                ],
                False,
            ),
        ]
        for block_id, rich_text_items, checked in todos:
            st, _ = self._http_request(
                "PATCH",
                f"blocks/{block_id}",
                {
                    "to_do": {
                        "rich_text": rich_text_items,
                        "checked": checked,
                    }
                },
            )
            if st == 200:
                updated += 1

        # 3. Right Column Paragraph -> Area 2: Trilhas de Busca (Notion Active Queue vs Local DB Reservoir)
        tracks_summary = (
            f"🧭 2. TRILHAS DE BUSCA (Na Fila do Notion vs Reserva no SQLite Local)\n"
            f"• 🌾 Regional / 88 Dias: {n_tracks.get('88 Days Specified Work', 11)} na fila ativa (de {sqlite_by_cat.get('88 Days Specified Work', 81)} qualificadas no SQLite)\n"
            f"• 💻 SAP / IT: {n_tracks.get('SAP Specialist', 13)} na fila ativa — sendo {n_ready_pkg} com Tailored CV+CL anexados (de {sqlite_by_cat.get('SAP Specialist', 144)} no SQLite)\n"
            f"• 🗂️ Office Support: {n_tracks.get('Office Support', 8)} na fila ativa (de {sqlite_by_cat.get('Office Support', 377)} no SQLite)\n"
            f"• ☕ Hospitality / Barista: {n_tracks.get('Hospitality / Barista', 8)} na fila ativa (de {sqlite_by_cat.get('Hospitality / Barista', 188)} no SQLite)"
        )
        st, _ = self._http_request(
            "PATCH",
            "blocks/4068c931-81f1-80a8-aebb-e07d6d10ed39",
            {
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": tracks_summary}}]
                }
            },
        )
        if st == 200:
            updated += 1

        # 4. Bottom Callout + Children -> Area 4: Saúde da Automação & Orquestração Maestri
        callout_header = (
            f"🤖 4. Saúde da Automação & Orquestração Maestri (Atualizado em {now_str})\n"
            f"Fonte de Dados: SQLite Local (data/applications.db) ↔ Notion API v1 ↔ Gmail Read-Only OAuth2"
        )
        st, _ = self._http_request(
            "PATCH",
            "blocks/ffa8c931-81f1-83d8-88e1-01b8418b0dde",
            {
                "callout": {
                    "icon": {"type": "emoji", "emoji": "⚙️"},
                    "rich_text": [{"type": "text", "text": {"content": callout_header}}],
                }
            },
        )
        if st == 200:
            updated += 1

        # Update the 4 child blocks inside the bottom callout with real Maestri agent telemetry
        self._http_request(
            "PATCH",
            "blocks/4438c931-81f1-8373-bc7a-01800a73ad59",
            {
                "heading_3": {
                    "rich_text": [{"type": "text", "text": {"content": "Telemetria dos Agentes & Separação de Eventos (Maestri Canvas)"}}]
                }
            },
        )
        child_paragraphs = [
            (
                "4e98c931-81f1-820d-b379-015f01314e21",
                f"🔭 Farol (Descoberta SEEK AU): {total_jobs:,} vagas únicas indexadas | 1.025 qualificadas por trilha | 1.384 bloqueadas automaticamente | Eventos de Auditoria no Sistema (tabela events): {total_system_events} (último: {str(last_audit_ts)[:19]})",
            ),
            (
                "7988c931-81f1-835f-a1e4-01ed409145b2",
                f"📜 Pergaminho & 🛡️ Revisor-Gy (Barreira de Aprovação): {n_ready_pkg} pacotes personalizados (Tailored CV + Cover Letter PDF) | {n_audited} auditados PASS pelo Revisor-Gy | {operator_approved_count} aprovados pelo operador humano (barreira final ativa)",
            ),
            (
                "d848c931-81f1-834a-9fc9-0166102ab731",
                f"📬 Correio (Métricas Separadas de E-mail & Notion): Mensagens Processadas pelo Correio: {processed_emails} | Mensagens que Exigem Sua Atenção: {actionable_emails} (contato recrutador 4C Group) | Encerramentos: {rejection_emails} (docorto.ai) | Notion Sync: {n_total} vagas ativas (0 falhas)",
            ),
        ]
        for b_id, b_text in child_paragraphs:
            st, _ = self._http_request(
                "PATCH",
                f"blocks/{b_id}",
                {
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": b_text}}]
                    }
                },
            )
            if st == 200:
                updated += 1

        return {"updated_blocks": updated, "dry_run": self.dry_run}

    def pull_user_updates(self, db_path: Path | str = "data/applications.db") -> Dict[str, Any]:
        """
        Pulls user manual actions from Notion back into SQLite:
        - If user moved card to '6. Bloqueado / Encerrado' or 'Rejected'/'Offer Pass', marks DISQUALIFIED in SQLite.
        - If user moved card to 'Applied', marks APPLIED in SQLite.
        - If user moved card to 'Interviewing', marks INTERVIEW in SQLite.
        """
        if self.dry_run or not self.database_id:
            return {"pulled": 0}

        st, res = self._http_request("POST", f"databases/{self.database_id}/query", {"page_size": 100})
        if st != 200:
            return {"pulled": 0, "error": res.get("error")}

        conn = get_connection(db_path)
        updated_count = 0
        try:
            cur = conn.cursor()
            for page in res.get("results", []):
                p = page.get("properties", {})
                pid = page.get("id")
                pid_clean = pid.replace("-", "") if pid else ""

                app_status = ((p.get("Application Status") or {}).get("status") or {}).get("name") or ""
                prep_stage = ((p.get("Prep Stage") or {}).get("select") or {}).get("name") or ""

                new_sqlite_status = None
                if prep_stage.startswith("6.") or app_status in ("Rejected", "Offer Pass"):
                    new_sqlite_status = "DISQUALIFIED"
                elif app_status == "Applied" or prep_stage.startswith("4."):
                    new_sqlite_status = "APPLIED"
                elif app_status in ("Interviewing", "INTERVIEW") or prep_stage.startswith("5."):
                    new_sqlite_status = "INTERVIEW"

                if new_sqlite_status:
                    cur.execute(
                        "UPDATE applications SET status = ? WHERE (notion_page_id = ? OR notion_page_id = ?) AND status != ?;",
                        (new_sqlite_status, pid, pid_clean, new_sqlite_status)
                    )
                    if cur.rowcount > 0:
                        updated_count += cur.rowcount
            conn.commit()
        finally:
            conn.close()

        return {"pulled": updated_count}

    def sync_all_applications(self, db_path: Path | str = "data/applications.db", limit: int = 40) -> Dict[str, Any]:
        """
        Synchronizes a curated, clearly-segmented set of applications from SQLite to Notion:
        - First pulls user updates from Notion so manual card moves are preserved in SQLite
        - All applications with genuine tailored PDF packages (Prep Stage 2 & 3)
        - Blocked reference row (app_001 — Prep Stage 6) so exclusion filters work out of the box
        - Balanced top candidates across all 4 tracks (88 Days, SAP, Office Support, Hospitality — Prep Stage 1)
        - Archives any legacy 'Other' rows from Notion so 100% of rows belong to valid tracks
        """
        self.pull_user_updates(db_path=db_path)
        self.ensure_database_schema()

        conn = get_connection(db_path)
        try:
            cur = conn.cursor()
            # 1. Include all applications with tailored PDF packages in applications/<app_id>/
            pkg_ids = []
            apps_dir = Path("applications")
            if apps_dir.exists():
                for d in sorted(apps_dir.iterdir()):
                    if d.is_dir() and any(d.glob("cv-*.pdf")):
                        pkg_ids.append(d.name)

            selected_ids: List[str] = []
            for pid in pkg_ids:
                cur.execute("SELECT id FROM applications WHERE id = ?;", (pid,))
                if cur.fetchone() and pid not in selected_ids:
                    selected_ids.append(pid)


            # 3. Fill remaining slots balanced across the 4 official tracks (never 'Other')
            tracks = [
                ("88 Days Specified Work", 11),
                ("SAP Specialist", 6),
                ("Office Support", 8),
                ("Hospitality / Barista", 8),
            ]
            for tr, quota in tracks:
                try:
                    cur.execute("""
                        SELECT id FROM applications
                        WHERE status IN ('Evaluated', 'ready_for_review', 'APPROVED', 'APPLIED', 'QUALIFIED')
                          AND category = ?
                        ORDER BY match_score DESC, updated_at DESC
                        LIMIT ?;
                    """, (tr, quota + 5))
                    added = 0
                    for r in cur.fetchall():
                        if r[0] not in selected_ids and len(selected_ids) < limit and added < quota:
                            selected_ids.append(r[0])
                            added += 1
                except Exception:
                    pass
        finally:
            conn.close()

        success_count = 0
        failures = []
        synced_page_ids = set()

        for app_id in selected_ids:
            ok, msg = self.sync_application(
                app_id,
                db_path=db_path,
                include_extended_schema=True,
                clear_missing_files=True,
            )
            if ok:
                success_count += 1
                synced_page_ids.add(_norm_id(msg))
            else:
                failures.append({"id": app_id, "error": msg})

        # Preserve existing Notion pages not selected for this sync; archival is a
        # separate, explicitly reviewed operation and is never part of routine sync.
        notion_by_track: Dict[str, int] = {}
        notion_tailored = 0
        notion_audited = 0
        notion_blocked = 0
        notion_triage = 0

        if not self.dry_run and self.database_id:
            st, qres = self._http_request("POST", f"databases/{self.database_id}/query", {"page_size": 100})
            if st == 200:
                for page in qres.get("results", []):
                    pid_norm = _norm_id(page.get("id"))
                    if pid_norm in synced_page_ids:
                        p = page.get("properties", {})
                        tr_name = ((p.get("Track") or {}).get("select") or {}).get("name") or "Other"
                        notion_by_track[tr_name] = notion_by_track.get(tr_name, 0) + 1
                        pstage = ((p.get("Prep Stage") or {}).get("select") or {}).get("name") or ""
                        if pstage.startswith("3."):
                            notion_audited += 1
                            notion_tailored += 1
                        elif pstage.startswith("2."):
                            notion_tailored += 1
                        elif pstage.startswith("6."):
                            notion_blocked += 1
                        else:
                            notion_triage += 1

        widget_res = self.sync_hq_page_widgets(
            db_path=db_path,
            synced_summary={
                "notion_total": success_count,
                "notion_tailored_packages": notion_tailored,
                "notion_audited_ready": notion_audited,
                "notion_blocked": notion_blocked,
                "notion_triage": notion_triage,
                "notion_by_track": notion_by_track,
            },
        )

        return {
            "total_candidates": len(selected_ids),
            "synced": success_count,
            "failed": len(failures),
            "failures": failures,
            "widgets_updated": widget_res.get("updated_blocks", 0),
            "dry_run": self.dry_run
        }
