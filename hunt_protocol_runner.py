"""
Protocolo Caçada Runner
Executes Phase 1 & 2 of the Hunting Protocol:
1. Varredura ativa nas duas trilhas (SAP e WHV 88 Dias) nos últimos 3 dias no SEEK AU.
2. Deduplicação contra data/applications.db e data/applications.md.
3. Filtragem de restrições de cidadania / NV1.
4. Cálculo de Match Score contra o perfil de Guilherme.
5. Inserção no banco SQLite e criação de cards no Notion em "1. Triagem (Para Avaliar)".
"""

import sys
import os
import json
import urllib.request
import urllib.parse
import hashlib
import datetime
from pathlib import Path
from typing import List, Dict, Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.cli.notion_sync import load_env_file
from src.database.connection import get_connection
from src.services.match_analyst import MatchAnalyst
from src.integrations.notion.adapter import NotionAdapter

QUERIES_TRACK_1_SAP = [
    {"track": "SAP Specialist", "role_track": "sap", "keywords": "SAP ABAP"},
    {"track": "SAP Specialist", "role_track": "sap", "keywords": "SAP BTP"},
    {"track": "SAP Specialist", "role_track": "sap", "keywords": "SAP Integration Developer"},
    {"track": "SAP Specialist", "role_track": "sap", "keywords": "SAP Fiori Developer"}
]

QUERIES_TRACK_2_88DAYS = [
    {"track": "88 Days Specified Work", "role_track": "operational", "keywords": "Farm Hand Accommodation"},
    {"track": "88 Days Specified Work", "role_track": "operational", "keywords": "Hospitality Cairns Staff Accommodation"},
    {"track": "88 Days Specified Work", "role_track": "operational", "keywords": "Resort Staff Accommodation Cairns"},
    {"track": "88 Days Specified Work", "role_track": "operational", "keywords": "Solar Farm Labourer"}
]

def fetch_seek_jobs(keywords: str, max_results: int = 15) -> List[Dict[str, Any]]:
    params = {
        "siteKey": "AU-Main",
        "where": "All Australia",
        "daterange": "3",
        "pageSize": str(max_results),
        "keywords": keywords
    }
    url = f"https://www.seek.com.au/api/jobsearch/v5/search?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("data", [])
    except Exception as e:
        print(f"  [SEEK FETCH ERROR] {keywords}: {e}")
        return []

def fetch_job_details(job_id: str) -> Dict[str, Any]:
    url = f"https://www.seek.com.au/api/jobsearch/v5/job/{job_id}?siteKey=AU-Main"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {}

def check_citizenship_blocker(text: str) -> bool:
    t = text.lower()
    blockers = [
        "australian citizen",
        "australian citizens",
        "citizens only",
        "citizen only",
        "only open to australian",
        "must be an australian citizen",
        "australian citizenship is required",
        "must hold australian citizenship",
        "nv1", "nv2", "baseline clearance", "security clearance required"
    ]
    return any(b in t for b in blockers)

def run_hunt() -> Dict[str, Any]:
    load_env_file()
    conn = get_connection()
    cur = conn.cursor()

    analyst = MatchAnalyst()
    adapter = NotionAdapter()

    # Get already applied external IDs / URLs
    cur.execute("SELECT j.external_id FROM applications a JOIN jobs j ON a.job_id = j.id")
    applied_ids = set(r[0] for r in cur.fetchall() if r[0])

    # Get existing discovered jobs
    cur.execute("SELECT external_id FROM jobs WHERE external_id IS NOT NULL")
    existing_job_ids = set(r[0] for r in cur.fetchall())

    cur.execute("SELECT canonical_url, id FROM jobs WHERE canonical_url IS NOT NULL")
    existing_urls = dict(cur.fetchall())

    hunt_results = {
        "sap": [],
        "whv_88days": [],
        "total_mined": 0,
        "qualified_new": 0,
        "notion_synced": 0
    }

    all_queries = QUERIES_TRACK_1_SAP + QUERIES_TRACK_2_88DAYS

    for q in all_queries:
        track = q["track"]
        role_track = q["role_track"]
        kw = q["keywords"]
        print(f"\n[VARREDURA] Minerando '{kw}' ({track})...")

        raw_jobs = fetch_seek_jobs(kw, max_results=10)
        for rj in raw_jobs:
            jid = str(rj.get("id", ""))
            if not jid:
                continue
            hunt_results["total_mined"] += 1

            if jid in applied_ids:
                continue

            job_url = f"https://www.seek.com.au/job/{jid}"

            title = rj.get("title", "") or "Untitled Job"
            adv_obj = rj.get("advertiser", {})
            company = adv_obj.get("description") if isinstance(adv_obj, dict) else None
            if not company:
                comp_obj = rj.get("company", {})
                company = comp_obj.get("name") if isinstance(comp_obj, dict) else comp_obj
            if not company:
                company = "Direct Employer"
            location = rj.get("location", "Australia") or "Australia"
            teaser = rj.get("teaser", "")

            # Fetch full details
            detail = fetch_job_details(jid)
            desc = detail.get("content") or detail.get("abstract") or teaser or ""
            full_text = f"{title}\n{company}\n{location}\n{desc}"

            # Evaluate using MatchAnalyst
            ev = analyst.evaluate_job(
                job_id=jid,
                title=title,
                description=desc,
                location=location,
                employer=company,
                role_track=role_track,
                category=track
            )

            # Check hard blockers
            if ev.hard_blockers or check_citizenship_blocker(full_text):
                continue

            score = ev.scaled_score_5

            # Minimum score threshold
            if score < 2.8:
                continue

            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            db_job_id = existing_urls.get(job_url, f"seek_{jid}")

            content_hash = hashlib.sha256((desc or title).encode("utf-8")).hexdigest()

            # Persist in SQLite jobs
            if job_url in existing_urls:
                cur.execute("UPDATE jobs SET last_verified_at = ? WHERE id = ?", (now_iso, db_job_id))
            else:
                cur.execute("""
                    INSERT INTO jobs (id, source, external_id, canonical_url, employer, title, location, description, content_hash, discovered_at, status)
                    VALUES (?, 'seek', ?, ?, ?, ?, ?, ?, ?, ?, 'DISCOVERED')
                """, (db_job_id, jid, job_url, company, title, location, desc[:2000], content_hash, now_iso))
                existing_urls[job_url] = db_job_id

            # Check if application already exists for this job
            cur.execute("SELECT id FROM applications WHERE job_id = ?", (db_job_id,))
            if cur.fetchone():
                continue

            app_id = f"app_seek_{jid}"
            profile_id = "sap_specialist" if role_track == "sap" else "operational_specialist"
            cur.execute("""
                INSERT INTO applications (id, job_id, profile_id, role_track, match_score, confidence, status, created_at, updated_at, category)
                VALUES (?, ?, ?, ?, ?, ?, '1. Triagem (Para Avaliar)', ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
            """, (app_id, db_job_id, profile_id, role_track, score, ev.confidence, now_iso, now_iso, track))

            item = {
                "id": app_id,
                "job_id": jid,
                "company": company,
                "title": title,
                "location": location,
                "score": score,
                "url": job_url,
                "category": track,
                "salary": detail.get("salary", "Competitive / Award")
            }

            if track == "SAP Specialist":
                hunt_results["sap"].append(item)
            else:
                hunt_results["whv_88days"].append(item)

            hunt_results["qualified_new"] += 1

    conn.commit()
    conn.close()

    # Sync top discovered cards to Notion
    if not adapter.dry_run and adapter.database_id:
        print("\n[CORREIO] Sincronizando novas vagas qualificadas no Notion...")
        sync_rep = adapter.sync_all_applications(limit=15)
        hunt_results["notion_synced"] = sync_rep.get("synced", 0)

    return hunt_results

if __name__ == "__main__":
    res = run_hunt()
    print("\n" + "="*50)
    print("      RESULTADO DO PROTOCOLO CAÇADA (FASE 1 & 2)      ")
    print("="*50)
    print(f"Total Vagas Mineradas   : {res['total_mined']}")
    print(f"Vagas Qualificadas Novas: {res['qualified_new']}")
    print(f"Cards no Notion Sincron : {res['notion_synced']}")
    print("\n--- TOP SAP QUALIFICADAS ---")
    for s in sorted(res['sap'], key=lambda x: x['score'], reverse=True)[:5]:
        print(f"• [{s['score']}/5.0] {s['company']} — {s['title']} ({s['location']})")
        print(f"  URL: {s['url']}")
    print("\n--- TOP 88 DIAS WHV QUALIFICADAS ---")
    for w in sorted(res['whv_88days'], key=lambda x: x['score'], reverse=True)[:5]:
        print(f"• [{w['score']}/5.0] {w['company']} — {w['title']} ({w['location']})")
        print(f"  URL: {w['url']}")
