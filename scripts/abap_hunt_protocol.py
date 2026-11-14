"""
Protocolo Caçada ABAP - Specialized Hunting Engine (Fase 1)
Run ID: CACADA-ABAP-20261103-01
Target: All active SAP ABAP / BTP / Fiori / CPI / Integration / S/4HANA roles across Australia.
Exhaustive search via SEEK API (daterange=30, pageSize=50) for 11 targeted queries.
Filters: Strict citizenship/security clearance exclusion.
Deduplication: Exclude roles already APPLIED / 4. APLICADO.
Persistence: Save qualified roles to data/applications.db (QUALIFIED, role_track='sap'),
             append to data/pipeline.md and data/scan-history.tsv.
"""

import sys
import os
import json
import urllib.request
import urllib.parse
import hashlib
import datetime
import re
import sqlite3
from pathlib import Path
from typing import List, Dict, Any, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

PROJECT_ROOT = Path(r"D:\AntigravityProjetos\career-ops")
sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = PROJECT_ROOT / "data" / "applications.db"
PIPELINE_PATH = PROJECT_ROOT / "data" / "pipeline.md"
SCAN_HISTORY_PATH = PROJECT_ROOT / "data" / "scan-history.tsv"

from src.database.connection import get_connection
from src.services.match_analyst import MatchAnalyst

SEARCH_QUERIES = [
    {"query": "SAP ABAP", "focus": "General ABAP"},
    {"query": "SAP ABAP Developer", "focus": "ABAP Developer"},
    {"query": "SAP ABAP Consultant", "focus": "ABAP Consultant"},
    {"query": "SAP S/4HANA ABAP", "focus": "S/4HANA ABAP"},
    {"query": "SAP BTP", "focus": "BTP Platform"},
    {"query": "SAP Fiori", "focus": "Fiori / UI5"},
    {"query": "SAP CPI", "focus": "Cloud Platform Integration"},
    {"query": "SAP Integration Developer", "focus": "Integration Suite"},
    {"query": "SAP Technical Consultant", "focus": "Technical Consultant"},
    {"query": "SAP OData", "focus": "OData / Gateway"},
    {"query": "SAP Forms Developer", "focus": "Forms (Adobe/SmartForms)"}
]

CITIZENSHIP_BLOCKERS = [
    "australian citizen",
    "australian citizens",
    "citizens only",
    "citizen only",
    "only open to australian",
    "must be an australian citizen",
    "australian citizenship is required",
    "must hold australian citizenship",
    "hold australian citizenship",
    "australian citizenship",
    "permanent resident or citizen",
    "citizenship required",
    "nv1", "nv2", "baseline clearance",
    "security clearance required",
    "defence security clearance",
    "secret clearance",
    "negative vetting",
    "agsva"
]

def check_citizenship(text: str) -> Tuple[bool, str]:
    t = text.lower()
    for b in CITIZENSHIP_BLOCKERS:
        if b in t:
            return True, b
    return False, ""

def classify_technical_flavor(title: str, text: str) -> Dict[str, Any]:
    t = f"{title}\n{text}".lower()
    
    classic_score = 0
    s4_score = 0
    btp_score = 0
    int_score = 0
    
    # Classic signals
    classic_keywords = [
        "abap", "alv", "enhancement", "badi", "user exit", "user-exit",
        "module pool", "dialog", "smartforms", "smart forms", "adobe forms",
        "bapi", "idoc", "rfc", "debugging", "st22", "sd", "mm", "fi", "co",
        "transport", "stms", "ecc", "forms"
    ]
    for kw in classic_keywords:
        if kw in t:
            classic_score += 1

    # S/4HANA modern syntax signals
    s4_keywords = [
        "s/4hana", "s4hana", "s/4 hana", "cds view", "cds views", "amdp",
        "odata", "sap gateway", "segw", "abap on hana", "inline declaration",
        "new syntax", "code remediation", "atc"
    ]
    for kw in s4_keywords:
        if kw in t:
            s4_score += 1.5

    # BTP / Fiori / UI5 signals
    btp_keywords = [
        "btp", "business technology platform", "rap", "restful application programming",
        "cap", "cloud application programming", "fiori", "fiori elements", "ui5",
        "node.js", "core data services with behavior definition", "cloud foundry"
    ]
    for kw in btp_keywords:
        if kw in t:
            btp_score += 1.5

    # Integration signals
    int_keywords = [
        "cpi", "cloud integration", "integration suite", "po", "pi", "apis", "rest api",
        "api management", "open connectors", "event mesh"
    ]
    for kw in int_keywords:
        if kw in t:
            int_score += 1.5

    # Determine dominant tier
    # Categories: Classic | S/4HANA | BTP/Fiori | Integration | Balanced
    if int_score >= 3 and int_score > btp_score and int_score > s4_score:
        tier = "Integration"
        tier_label = "SAP Integration (CPI / APIs / Suite)"
    elif btp_score >= 3 and btp_score > s4_score:
        tier = "BTP/Fiori"
        tier_label = "SAP BTP / Fiori / UI5"
    elif s4_score >= 3:
        tier = "S/4HANA"
        tier_label = "S/4HANA Modern ABAP (CDS / OData / New Syntax)"
    elif classic_score >= 2:
        tier = "Classic"
        tier_label = "Classic ABAP Core (Enhancements/Reports/Forms)"
    else:
        tier = "S/4HANA" if "s/4" in t or "s4" in t else "Classic"
        tier_label = "Enterprise SAP Technical"

    return {
        "tier": tier,
        "tier_label": tier_label,
        "classic_score": classic_score,
        "s4_score": s4_score,
        "btp_score": btp_score,
        "integration_score": int_score
    }

def fetch_seek_search(query: str, daterange: str = "30", page_size: int = 50) -> List[Dict[str, Any]]:
    params = {
        "siteKey": "AU-Main",
        "where": "All Australia",
        "daterange": daterange,
        "pageSize": str(page_size),
        "keywords": query
    }
    url = f"https://www.seek.com.au/api/jobsearch/v5/search?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("data", [])
    except Exception as e:
        print(f"  [SEEK SEARCH ERROR] '{query}': {e}")
        return []

def fetch_seek_job_detail(job_id: str) -> Dict[str, Any]:
    url = f"https://www.seek.com.au/api/jobsearch/v5/job/{job_id}?siteKey=AU-Main"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}

def extract_locations(j: Dict[str, Any]) -> Tuple[str, str]:
    locations_list = j.get("locations", [])
    full_loc_strs = []
    
    for loc in locations_list:
        label = loc.get("label", "")
        if label:
            full_loc_strs.append(label)
        for hier in loc.get("seoHierarchy", []):
            c_name = hier.get("contextualName", "")
            if c_name:
                full_loc_strs.append(c_name)
                    
    combined = ", ".join(list(dict.fromkeys(full_loc_strs)))
    if not combined:
        loc_h = j.get("locationHierarchy", {})
        if loc_h and loc_h.get("city"):
            combined = f"{loc_h.get('city')}, {loc_h.get('state', 'AU')}"
        else:
            combined = j.get("location", "Australia")
    return combined

def run_abap_hunt():
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    
    analyst = MatchAnalyst()

    # 1. Identify jobs already APPLIED to deduplicate
    cur.execute("""
        SELECT j.external_id, j.canonical_url 
        FROM applications a 
        JOIN jobs j ON a.job_id = j.id 
        WHERE a.status IN ('APPLIED', '4. APLICADO (Candidatura Enviada)');
    """)
    applied_external_ids = set()
    applied_urls = set()
    for ext_id, c_url in cur.fetchall():
        if ext_id:
            applied_external_ids.add(str(ext_id))
        if c_url:
            applied_urls.add(str(c_url).strip().lower())

    # Map existing jobs
    cur.execute("SELECT id, external_id, canonical_url, status FROM jobs")
    existing_jobs_by_id = {}
    existing_jobs_by_url = {}
    for j_id, ext_id, c_url, st in cur.fetchall():
        if ext_id:
            existing_jobs_by_id[str(ext_id)] = j_id
        if c_url:
            existing_jobs_by_url[str(c_url).strip().lower()] = j_id

    mined_jobs = {}
    total_raw_found = 0

    print("=" * 75)
    print("  PROTOCOLO CAÇADA ABAP — FASE 1 (BUSCA EXAUSTIVA AU DATERANGE=30)")
    print("=" * 75)

    for item in SEARCH_QUERIES:
        q = item["query"]
        focus = item["focus"]
        print(f"[QUERY] Buscando '{q}' ({focus}) [daterange=30, pageSize=50]...", end=" ", flush=True)
        raw_list = fetch_seek_search(q, daterange="30", page_size=50)
        print(f"-> {len(raw_list)} vagas")
        total_raw_found += len(raw_list)

        for rj in raw_list:
            jid = str(rj.get("id", ""))
            if not jid:
                continue
            if jid not in mined_jobs:
                mined_jobs[jid] = rj

    print(f"\n[DEDUPLICAÇÃO] Total único de vagas mineradas na Austrália: {len(mined_jobs)}")

    qualified_jobs = []
    blocked_citizenship = []
    skipped_applied = 0

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    today_str = datetime.date.today().isoformat()

    pipeline_additions = []
    history_additions = []

    for jid, rj in mined_jobs.items():
        job_url = f"https://www.seek.com.au/job/{jid}"
        url_lower = job_url.lower()

        # Deduplication against already applied
        if jid in applied_external_ids or url_lower in applied_urls:
            skipped_applied += 1
            continue

        title = rj.get("title", "") or "SAP Role"
        
        # Company
        adv_obj = rj.get("advertiser", {})
        company = adv_obj.get("description") if isinstance(adv_obj, dict) else None
        if not company:
            comp_obj = rj.get("company", {})
            company = comp_obj.get("name") if isinstance(comp_obj, dict) else comp_obj
        if not company:
            company = "Enterprise SAP Client"

        location = extract_locations(rj)
        teaser = rj.get("teaser", "")
        bullets = " ".join(rj.get("bulletPoints", []))

        # Check full text snippet first for fast citizenship filter
        quick_text = f"{title} {company} {location} {teaser} {bullets}"
        is_blocked, blocker_reason = check_citizenship(quick_text)
        if is_blocked:
            blocked_citizenship.append({"jid": jid, "title": title, "company": company, "reason": blocker_reason})
            continue

        # Fetch detail for full analysis
        detail = fetch_seek_job_detail(jid)
        desc = detail.get("content") or detail.get("abstract") or teaser or bullets or ""
        salary = detail.get("salary", "") or "Market Rate (Contract/Perm)"
        work_type = detail.get("workType", "") or "Contract / Permanent"

        full_text = f"{title}\n{company}\n{location}\n{desc}"

        # Re-check citizenship on full description
        is_blocked, blocker_reason = check_citizenship(full_text)
        if is_blocked:
            blocked_citizenship.append({"jid": jid, "title": title, "company": company, "reason": blocker_reason})
            continue

        # Verify it is an authentic SAP role
        t_lower = (title + " " + desc).lower()
        if not any(k in t_lower for k in ["sap", "abap", "btp", "fiori", "s/4hana", "s4hana", "cpi", "odata"]):
            continue

        # Technical classification
        flavor = classify_technical_flavor(title, desc)
        tier = flavor["tier"]
        tier_label = flavor["tier_label"]

        # Match Analyst Score
        ev = analyst.evaluate_job(
            job_id=jid,
            title=title,
            description=desc,
            location=location,
            employer=company,
            role_track="sap",
            category="SAP Specialist"
        )
        base_score = ev.scaled_score_5
        
        # Calculate fit adjustment based on Guilherme's SAP strengths
        if tier == "Classic":
            adjusted_score = min(5.0, base_score + 0.5)
        elif tier == "S/4HANA":
            adjusted_score = min(5.0, base_score + 0.4)
        elif tier == "Integration":
            adjusted_score = min(5.0, base_score + 0.3)
        else: # BTP/Fiori
            adjusted_score = min(5.0, max(3.0, base_score + 0.1))

        # Database ID resolution
        db_job_id = existing_jobs_by_id.get(jid, existing_jobs_by_url.get(url_lower, f"seek_{jid}"))
        app_id = f"app_{db_job_id}"
        content_hash = hashlib.sha256((desc or title).encode("utf-8")).hexdigest()
        pub_date = rj.get("listingDate") or now_iso

        # Upsert jobs table
        cur.execute("""
            INSERT INTO jobs (
                id, source, external_id, canonical_url, employer,
                title, location, description, publication_date,
                discovered_at, status, content_hash
            ) VALUES (?, 'seek', ?, ?, ?, ?, ?, ?, ?, ?, 'QUALIFIED', ?)
            ON CONFLICT(id) DO UPDATE SET
                canonical_url=excluded.canonical_url,
                employer=excluded.employer,
                title=excluded.title,
                location=excluded.location,
                description=excluded.description,
                status='QUALIFIED',
                content_hash=excluded.content_hash,
                last_verified_at=excluded.discovered_at;
        """, (
            db_job_id, jid, job_url, company,
            title, location, desc[:3500], pub_date,
            now_iso, content_hash
        ))

        # Upsert applications table
        cur.execute("""
            INSERT INTO applications (
                id, job_id, profile_id, role_track, match_score,
                confidence, status, category, visa_fit, created_at, updated_at
            ) VALUES (?, ?, 'default_profile', 'sap', ?, ?, 'QUALIFIED', 'SAP Specialist', 'eligible_contract', ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                role_track='sap',
                match_score=excluded.match_score,
                confidence=excluded.confidence,
                status='QUALIFIED',
                category='SAP Specialist',
                visa_fit='eligible_contract',
                updated_at=excluded.updated_at;
        """, (
            app_id, db_job_id, adjusted_score, ev.confidence, now_iso, now_iso
        ))

        pipeline_additions.append(f"- [ ] {job_url} | {company} | {title} | {location} | posted: {pub_date[:10]}\n")
        history_additions.append(f"{now_iso}\t{job_url}\tqualified\t{company}\t{title}\n")

        qualified_jobs.append({
            "seek_id": jid,
            "title": title,
            "company": company,
            "location": location,
            "tier": tier,
            "tier_label": tier_label,
            "score": adjusted_score,
            "salary": salary,
            "work_type": work_type,
            "url": job_url
        })

    conn.commit()
    conn.close()

    # Append to pipeline.md and scan-history.tsv
    if pipeline_additions:
        with open(PIPELINE_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n\n## Caçada ABAP Fase 1 ({today_str})\n")
            f.writelines(pipeline_additions)

    if history_additions:
        with open(SCAN_HISTORY_PATH, "a", encoding="utf-8") as f:
            f.writelines(history_additions)

    # Sort qualified by score descending
    qualified_jobs.sort(key=lambda x: x["score"], reverse=True)

    print("\n" + "=" * 75)
    print("                 ESTATÍSTICAS DA CAÇADA ABAP                  ")
    print("=" * 75)
    print(f"Total Bruto Minerado na API SEEK    : {total_raw_found}")
    print(f"Total Único Após Deduplicação       : {len(mined_jobs)}")
    print(f"Ignoradas (Já Marcadas como APPLIED): {skipped_applied}")
    print(f"Bloqueadas (Cidadania / Clearance) : {len(blocked_citizenship)}")
    print(f"TOTAL SAP QUALIFICADAS E ELEGÍVEIS  : {len(qualified_jobs)}")
    print("=" * 75)

    # Return results
    return {
        "total_mined": len(mined_jobs),
        "skipped_applied": skipped_applied,
        "blocked_citizenship": len(blocked_citizenship),
        "qualified_jobs": qualified_jobs
    }

if __name__ == "__main__":
    res = run_abap_hunt()
    q_list = res["qualified_jobs"]
    print(f"\nTotal Vagas Elegíveis: {len(q_list)}")
