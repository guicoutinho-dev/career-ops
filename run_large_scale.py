"""
Large-Scale Multi-Track Pipeline Orchestrator for Australia Job Hunter.
Priorities:
1. 88 Days WHV Specified Work (Agriculture, Farm Hand, Fruit Picking, Meat Processing, Construction, Solar Farm)
2. SAP / ABAP / BTP / S/4HANA (Contract & Permanent)
3. Office Support / Administration / Data Entry
4. Hospitality / Barista / Cafe All-Rounder / Food & Beverage Attendant
"""

import sys
import json
import urllib.request
import urllib.parse
import hashlib
import datetime
import time
from pathlib import Path
from src.database.connection import get_connection
from src.services.match_analyst import MatchAnalyst
from src.integrations.notion.adapter import NotionAdapter

SEARCH_TRACKS = [
    # ── PRIORITY 1: 88 DAYS SPECIFIED WORK (AGRICULTURE & REGIONAL) ─────────
    {
        "category": "88_days",
        "role_track": "operational",
        "profile_id": "operational_specialist",
        "queries": [
            "Farm Hand",
            "Fruit Picker",
            "Fruit Picking",
            "Harvest Worker",
            "Agricultural Worker",
            "Solar Farm",
            "Solar Farm Labourer",
            "Meat Worker",
            "Abattoir Worker",
            "Station Hand",
            "Packhouse Worker",
            "Grain Harvest"
        ],
        "locations": ["All Australia", "Regional NSW", "NSW", "QLD", "VIC", "WA", "SA", "NT", "TAS"]
    },
    # ── PRIORITY 2: SAP / ABAP SPECIALIST ───────────────────────────────────
    {
        "category": "sap_specialist",
        "role_track": "sap",
        "profile_id": "sap_specialist",
        "queries": [
            "SAP ABAP",
            "SAP BTP",
            "SAP Integration",
            "SAP Consultant",
            "SAP S/4HANA",
            "SAP Fiori",
            "SAP Technical Consultant",
            "SAP Developer"
        ],
        "locations": ["All Australia", "Sydney NSW", "Melbourne VIC", "Brisbane QLD", "Perth WA", "Canberra ACT"]
    },
    # ── PRIORITY 3: OFFICE SUPPORT / ADMIN ──────────────────────────────────
    {
        "category": "office_support",
        "role_track": "operational",
        "profile_id": "operational_specialist",
        "queries": [
            "Administration Assistant",
            "Office Support",
            "Data Entry",
            "Office Administrator",
            "Receptionist",
            "Customer Service"
        ],
        "locations": ["All Australia", "Sydney NSW", "Melbourne VIC", "Brisbane QLD"]
    },
    # ── PRIORITY 4: HOSPITALITY / BARISTA ───────────────────────────────────
    {
        "category": "hospitality",
        "role_track": "operational",
        "profile_id": "operational_specialist",
        "queries": [
            "Barista",
            "Cafe All Rounder",
            "Hospitality All Rounder",
            "Food and Beverage Attendant",
            "Bartender"
        ],
        "locations": ["All Australia", "Sydney NSW", "Melbourne VIC", "Brisbane QLD"]
    }
]

def fetch_seek(keywords: str, location: str = "All Australia", daterange: str = "7"):
    params = {
        "siteKey": "AU-Main",
        "where": location,
        "daterange": daterange,
        "pageSize": "30",
        "keywords": keywords
    }
    url = "https://www.seek.com.au/api/jobsearch/v5/search?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("data", [])
    except Exception:
        return []

def main():
    log_file = Path("reports/large_scale_pipeline.log")
    log_file.parent.mkdir(parents=True, exist_ok=True)

    def log(msg):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {msg}"
        print(line, flush=True)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    log("="*70)
    log("   INICIANDO PROCESSAMENTO EM LARGA ESCALA — AUSTRALIA JOB HUNTER   ")
    log("="*70)

    conn = get_connection()
    analyst = MatchAnalyst()
    notion = NotionAdapter(dry_run=False)

    total_scanned = 0
    total_ingested = 0
    total_qualified = 0

    for track in SEARCH_TRACKS:
        category = track["category"]
        role_track = track["role_track"]
        profile_id = track["profile_id"]
        log(f"\n>>> TRILHA ATIVA: {category.upper()} (Track: {role_track})")

        for loc in track["locations"]:
            for q in track["queries"]:
                time.sleep(0.35)
                jobs = fetch_seek(q, location=loc, daterange="7")
                total_scanned += len(jobs)
                new_in_query = 0

                cur = conn.cursor()
                for j in jobs:
                    raw_id = str(j.get("id"))
                    job_id = f"seek_{raw_id}"
                    canonical_url = f"https://www.seek.com.au/job/{raw_id}"

                    # Evitar duplicata
                    cur.execute("SELECT id FROM jobs WHERE canonical_url = ?;", (canonical_url,))
                    if cur.fetchone():
                        continue

                    title = j.get("title", "Untitled Role")
                    advertiser = j.get("advertiser", {})
                    employer = advertiser.get("description", "Undisclosed") if advertiser else "Undisclosed"

                    loc_h = j.get("locationHierarchy", {})
                    if loc_h and loc_h.get("city"):
                        job_location = f"{loc_h.get('city')}, {loc_h.get('state', 'AU')}"
                    else:
                        job_location = j.get("location", loc)

                    teaser = j.get("teaser") or ""
                    bullet_points = " ".join(j.get("bulletPoints", []))
                    description = f"{teaser} {bullet_points}".strip() or title
                    content_hash = hashlib.sha256(f"{title} {employer} {description}".lower().encode("utf-8")).hexdigest()
                    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    pub_date = j.get("listingDate") or now_iso

                    # Inserir vaga
                    cur.execute("""
                        INSERT INTO jobs (
                            id, source, external_id, canonical_url, employer,
                            title, location, description, publication_date,
                            discovered_at, status, content_hash
                        ) VALUES (?, 'seek', ?, ?, ?, ?, ?, ?, ?, ?, 'DISCOVERED', ?);
                    """, (job_id, raw_id, canonical_url, employer, title, job_location, description, pub_date, now_iso, content_hash))

                    app_id = f"app_{job_id}"
                    cur.execute("""
                        INSERT INTO applications (
                            id, job_id, profile_id, role_track, match_score,
                            confidence, status, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, NULL, NULL, 'DISCOVERED', ?, ?);
                    """, (app_id, job_id, profile_id, role_track, now_iso, now_iso))

                    # Avaliação pelo MatchAnalyst
                    score_res = analyst.evaluate_job(
                        job_id=job_id,
                        title=title,
                        employer=employer,
                        description=description,
                        location=job_location,
                        role_track=role_track,
                        publication_date=pub_date
                    )

                    new_status = "QUALIFIED" if score_res.recommendation == "AUTO_QUALIFY" else "DISQUALIFIED"
                    cur.execute("""
                        UPDATE applications
                        SET match_score = ?, confidence = ?, status = ?, updated_at = ?
                        WHERE id = ?;
                    """, (score_res.scaled_score_5, score_res.confidence, new_status, now_iso, app_id))

                    if new_status == "QUALIFIED":
                        total_qualified += 1
                        try:
                            notion.sync_application(app_id)
                        except Exception:
                            pass

                    total_ingested += 1
                    new_in_query += 1

                conn.commit()
                if new_in_query > 0:
                    log(f"  [{category.upper()}] '{q}' em {loc}: +{new_in_query} novas vagas (Total banco: {total_ingested})")

    conn.close()
    log("\n" + "="*70)
    log(f"VARREDURA NOTURNA CONCLUÍDA COM SUCESSO!")
    log(f"Total Analisado:   {total_scanned}")
    log(f"Novas Ingeridas:   {total_ingested}")
    log(f"Novas Qualificadas: {total_qualified}")
    log("="*70)

if __name__ == "__main__":
    main()
