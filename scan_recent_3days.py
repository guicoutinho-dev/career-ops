"""
Active Job Discovery for Australia (last 3 days).
Queries SEEK AU API and persists directly into authoritative SQLite database.
Tracks:
- SAP (ABAP, BTP, Integration, Consultant, S/4HANA)
- Operational (Warehouse, Storeperson, Forklift, Logistics, Picker Packer)
"""

import sys
import json
import urllib.request
import urllib.parse
import hashlib
import datetime
from pathlib import Path
from src.database.connection import get_connection

QUERIES = [
    {"track": "sap", "keywords": "SAP ABAP"},
    {"track": "sap", "keywords": "SAP BTP"},
    {"track": "sap", "keywords": "SAP Integration"},
    {"track": "sap", "keywords": "SAP Consultant"},
    {"track": "sap", "keywords": "SAP S/4HANA"},
    {"track": "operational", "keywords": "Warehouse Storeperson"},
    {"track": "operational", "keywords": "Forklift Driver"},
    {"track": "operational", "keywords": "Storeperson"},
    {"track": "operational", "keywords": "Picker Packer"}
]

def fetch_seek(keywords: str):
    params = {
        "siteKey": "AU-Main",
        "where": "All Australia",
        "daterange": "3",
        "pageSize": "30",
        "keywords": keywords
    }
    url = f"https://www.seek.com.au/api/jobsearch/v5/search?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("data", [])
    except Exception as e:
        print(f"  [ERROR] {e}")
        return []

def main():
    print("==================================================")
    print("   BUSCA ATIVA DE VAGAS NA AUSTRÁLIA (ÚLTIMOS 3 DIAS)")
    print("==================================================")

    conn = get_connection()
    cur = conn.cursor()

    total_scanned = 0
    new_jobs_added = 0
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    new_list = []

    for q in QUERIES:
        track = q["track"]
        kw = q["keywords"]
        print(f"Buscando [{track.upper()}] '{kw}'...", end=" ", flush=True)
        jobs = fetch_seek(kw)
        print(f"Encontradas: {len(jobs)}")
        total_scanned += len(jobs)

        for j in jobs:
            raw_id = str(j.get("id"))
            job_id = f"seek_{raw_id}"
            canonical_url = f"https://www.seek.com.au/job/{raw_id}"
            title = j.get("title", "Untitled Role")
            advertiser = j.get("advertiser", {})
            employer = advertiser.get("description", "Undisclosed") if advertiser else "Undisclosed"

            loc_h = j.get("locationHierarchy", {})
            if loc_h and loc_h.get("city"):
                location = f"{loc_h.get('city')}, {loc_h.get('state', 'AU')}"
            else:
                location = j.get("location", "Australia")

            teaser = j.get("teaser") or ""
            bullet_points = " ".join(j.get("bulletPoints", []))
            description = f"{teaser} {bullet_points}".strip() or title
            content_hash = hashlib.sha256(f"{title} {employer} {description}".lower().encode("utf-8")).hexdigest()
            pub_date = j.get("listingDate") or now_iso

            # Check if job already exists
            cur.execute("SELECT id FROM jobs WHERE canonical_url = ?;", (canonical_url,))
            existing = cur.fetchone()
            if not existing:
                cur.execute("""
                    INSERT INTO jobs (
                        id, source, external_id, canonical_url, employer,
                        title, location, description, publication_date,
                        discovered_at, status, content_hash
                    ) VALUES (?, 'seek', ?, ?, ?, ?, ?, ?, ?, ?, 'DISCOVERED', ?);
                """, (
                    job_id, raw_id, canonical_url, employer,
                    title, location, description, pub_date,
                    now_iso, content_hash
                ))

                app_id = f"app_{job_id}"
                profile_id = "sap_specialist" if track == "sap" else "operational_specialist"
                cur.execute("""
                    INSERT INTO applications (
                        id, job_id, profile_id, role_track, match_score,
                        confidence, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, NULL, NULL, 'DISCOVERED', ?, ?);
                """, (app_id, job_id, profile_id, track, now_iso, now_iso))

                new_jobs_added += 1
                new_list.append({
                    "id": app_id,
                    "title": title,
                    "employer": employer,
                    "location": location,
                    "track": track,
                    "url": canonical_url
                })

    conn.commit()
    conn.close()

    print("\n==================================================")
    print(f"Total de Vagas Escaneadas (últimos 3 dias): {total_scanned}")
    print(f"Novas Vagas Inéditas Cadastradas no Banco:   {new_jobs_added}")
    print("==================================================")

    if new_list:
        print("\nTop Novas Vagas Descobertas:")
        for item in new_list[:12]:
            print(f"- [{item['track'].upper()}] {item['title']} @ {item['employer']} ({item['location']})")
            print(f"  URL: {item['url']}")

if __name__ == "__main__":
    main()
