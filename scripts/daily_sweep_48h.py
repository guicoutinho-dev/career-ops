"""
Daily active job sweep for Australia (last 24-48h).
Covers:
1. SAP Track: ABAP, BTP, Fiori, S/4HANA, Integration (Pure SAP specialist roles)
2. WHV 462 Track (88 Days):
   - Regional Hospitality (strictly Northern Australia - DHA Table 1)
   - Farm Hand, Harvest & Agriculture (Regional Australia - DHA Table 4)
   - Solar Farm / Regional Construction (Regional Australia)
Filters citizenship/clearance blockers, tractor/forklift/mandatory RSA requirements,
deduplicates against data/applications.db, inserts into SQLite, and syncs with Notion.
"""

import sys
import os
import json
import urllib.request
import urllib.parse
import hashlib
import datetime
import re
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

PROJECT_ROOT = Path(r"D:\AntigravityProjetos\career-ops")
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from src.database.connection import get_connection
from src.integrations.notion.adapter import NotionAdapter

DB_PATH = PROJECT_ROOT / "data" / "applications.db"
PIPELINE_PATH = PROJECT_ROOT / "data" / "pipeline.md"
SCAN_HISTORY_PATH = PROJECT_ROOT / "data" / "scan-history.tsv"

CITIZENSHIP_BLOCKERS = [
    "australian citizen",
    "australian citizens",
    "citizens only",
    "citizen only",
    "only open to australian",
    "must be an australian citizen",
    "australian citizenship is required",
    "must hold australian citizenship",
    "permanent resident or citizen",
    "citizenship required",
    "nv1", "nv2", "baseline clearance",
    "security clearance required",
    "negative vetting",
    "defence clearance",
    "agsva"
]

METRO_EXCLUSION_POSTCODES = set(range(2000, 2235)) | set(range(3000, 3208)) | set(range(4000, 4124)) | set(range(6000, 6039)) | set(range(6050, 6085)) | set(range(6090, 6183)) | set(range(6210, 6215))

# Northern Australia postcodes (DHA Table 1 for Tourism/Hospitality):
# QLD North of Tropic of Capricorn (4699-4899), NT (0800-0899), WA North of Tropic of Capricorn (6701-6770)
NORTHERN_REMOTE_POSTCODE_PREFIXES = ("08", "47", "48", "67")

DISQUALIFIER_PATTERNS = [
    (re.compile(r"\b(tractor\s+operator|tractor\s+driver|tractor\s+licen[cs]e|operating\s+tractors?)\b", re.I), "tractor_required"),
    (re.compile(r"\b(forklift|forklifts|fork lift|lf licen[cs]e)\b", re.I), "forklift_required"),
    (re.compile(r"\b(mr licen[cs]e|hr licen[cs]e|hc licen[cs]e|mc licen[cs]e|heavy rigid|medium rigid|truck licen[cs]e)\b", re.I), "truck_required"),
    (re.compile(r"\b(must hold|must have|mandatory|current|require[ds]?)\s+rsa\b", re.I), "mandatory_rsa"),
    (re.compile(r"\brsa\s+(required|mandatory|is mandatory|is essential|must be held)\b", re.I), "mandatory_rsa"),
    (re.compile(r"responsible service of alcohol.*\(mandatory\)", re.I), "mandatory_rsa")
]

def check_citizenship_blocker(text):
    t = text.lower()
    for b in CITIZENSHIP_BLOCKERS:
        if b in t:
            return True, b
    return False, None

def check_operational_disqualifiers(text):
    clean = re.sub(r"<[^>]+>", " ", text or "")
    for pattern, name in DISQUALIFIER_PATTERNS:
        m = pattern.search(clean)
        if m:
            return True, f"{name} ('{m.group(0)}')"
    return False, None

def is_genuine_sap_role(title, desc=""):
    t_lower = title.lower()
    sap_title_match = any(k in t_lower for k in ["sap", "abap", "btp", "fiori", "s/4hana", "s4hana", "cpi"])
    if not sap_title_match:
        return False, "SAP not explicitly in job title"
    return True, "Genuine SAP role"

def fetch_seek(keywords, daterange="2", page_size=30):
    params = {
        "siteKey": "AU-Main",
        "where": "All Australia",
        "daterange": daterange,
        "pageSize": str(page_size),
        "keywords": keywords
    }
    url = f"https://www.seek.com.au/api/jobsearch/v5/search?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("data", [])
    except Exception as e:
        print(f"  [ERROR] Seeking '{keywords}': {e}")
        return []

def extract_locations(j):
    locations_list = j.get("locations", [])
    full_loc_strs = []
    postcode = None
    
    for loc in locations_list:
        label = loc.get("label", "")
        if label:
            full_loc_strs.append(label)
        for hier in loc.get("seoHierarchy", []):
            c_name = hier.get("contextualName", "")
            if c_name:
                full_loc_strs.append(c_name)
                m = re.search(r'\b(\d{4})\b', c_name)
                if m and not postcode:
                    postcode = int(m.group(1))
                    
    combined = ", ".join(list(dict.fromkeys(full_loc_strs)))
    if not combined:
        loc_h = j.get("locationHierarchy", {})
        if loc_h and loc_h.get("city"):
            combined = f"{loc_h.get('city')}, {loc_h.get('state', 'AU')}"
        else:
            combined = j.get("location", "Australia")
    return combined, postcode

def main():
    print("======================================================================")
    print("  VARREDURA DIÁRIA DE VAGAS NA AUSTRÁLIA (ÚLTIMAS 24-48H) — WHV & SAP  ")
    print("======================================================================")
    
    conn = get_connection(DB_PATH)
    cur = conn.cursor()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    
    # 1. Queries por Trilha
    sap_queries = [
        "SAP ABAP",
        "SAP BTP",
        "SAP Fiori",
        "SAP S/4HANA",
        "SAP Integration",
        "SAP Consultant",
        "SAP Developer"
    ]
    
    whv_queries = [
        "\"88 days\"",
        "\"working holiday\" farm",
        "\"farm hand\"",
        "harvest labourer",
        "agriculture labourer",
        "\"solar farm\"",
        "\"whitsundays\" hospitality",
        "\"port douglas\" resort",
        "\"cairns\" hospitality",
        "\"darwin\" hospitality",
        "\"broome\" resort",
        "\"airlie beach\"",
        "\"hamilton island\"",
        "\"kununurra\"",
        "\"exmouth\""
    ]
    
    all_jobs_raw = []
    
    print("\n--- 1. Buscando Trilha SAP (Últimas 48h) ---")
    for q in sap_queries:
        print(f"Buscando SAP '{q}'...", end=" ", flush=True)
        jobs = fetch_seek(q, daterange="2", page_size=30)
        print(f"Encontradas: {len(jobs)}")
        for j in jobs:
            j["_track"] = "sap"
            all_jobs_raw.append(j)
            
    print("\n--- 2. Buscando Trilha 88 Dias WHV 462 (Últimas 48h) ---")
    for q in whv_queries:
        print(f"Buscando WHV '{q}'...", end=" ", flush=True)
        jobs = fetch_seek(q, daterange="2", page_size=30)
        print(f"Encontradas: {len(jobs)}")
        for j in jobs:
            j["_track"] = "whv"
            all_jobs_raw.append(j)
            
    unique_raw = {}
    for j in all_jobs_raw:
        jid = str(j.get("id"))
        if jid not in unique_raw:
            unique_raw[jid] = j
            
    print(f"\nTotal bruto de vagas únicas coletadas: {len(unique_raw)}")
    
    scanned_count = len(unique_raw)
    new_sap_qualified = []
    new_sap_blocked = []
    new_whv_qualified = []
    new_whv_blocked = []
    already_tracked = 0
    
    pipeline_additions = []
    history_additions = []
    
    for raw_id, j in unique_raw.items():
        canonical_url = f"https://www.seek.com.au/job/{raw_id}"
        track = j["_track"]
        title = j.get("title", "Untitled Role")
        advertiser = j.get("advertiser", {})
        employer = advertiser.get("description", "Undisclosed") if advertiser else "Undisclosed"
        
        loc_str, postcode = extract_locations(j)
        teaser = j.get("teaser") or ""
        bullets = " ".join(j.get("bulletPoints", []))
        full_text = f"{title} {teaser} {bullets} {loc_str}"
        content_hash = hashlib.sha256(f"{title} {employer} {full_text}".lower().encode("utf-8")).hexdigest()
        pub_date = j.get("listingDate") or now_iso
        job_id = f"seek_{raw_id}"
        app_id = f"app_{job_id}"
        
        # Deduplication check
        cur.execute("SELECT id, status FROM jobs WHERE id = ? OR canonical_url = ?;", (job_id, canonical_url))
        existing = cur.fetchone()
        if existing:
            already_tracked += 1
            continue
            
        has_blocker, blocker_reason = check_citizenship_blocker(full_text)
        
        if track == "sap":
            is_sap, sap_reason = is_genuine_sap_role(title, full_text)
            if not is_sap:
                job_status = "DISQUALIFIED"
                visa_fit = "not_sap_specialist"
                match_score = 1.0
                category = "Other"
                new_sap_blocked.append({"title": title, "employer": employer, "reason": sap_reason, "url": canonical_url})
            elif has_blocker:
                job_status = "DISQUALIFIED"
                visa_fit = "blocked_clearance"
                match_score = 2.0
                category = "SAP Specialist"
                new_sap_blocked.append({
                    "id": raw_id, "title": title, "employer": employer,
                    "loc": loc_str, "reason": blocker_reason, "url": canonical_url
                })
            else:
                job_status = "QUALIFIED"
                visa_fit = "eligible_contract"
                match_score = 4.8
                category = "SAP Specialist"
                new_sap_qualified.append({
                    "id": raw_id, "title": title, "employer": employer,
                    "loc": loc_str, "url": canonical_url
                })
        else: # WHV 462
            is_hospitality = any(k in full_text.lower() for k in [
                "hospitality", "resort", "hotel", "housekeeper", "cleaner", "kitchen hand",
                "cook", "chef", "barista", "guest service", "motel", "pub", "cottages"
            ])
            is_agro = any(k in full_text.lower() for k in [
                "farm", "harvest", "crop", "agriculture", "orchard", "picking", "packing",
                "cotton", "grain", "station hand", "cattle", "apiary", "beekeeper", "dairy", "livestock"
            ])
            is_solar = any(k in full_text.lower() for k in ["solar farm", "solar installer", "solar labourer"])
            
            # Check operational disqualifiers (tractor/forklift/mandatory RSA)
            has_op_disq, op_disq_reason = check_operational_disqualifiers(full_text)
            
            # Metro check
            is_metro = False
            if postcode and postcode in METRO_EXCLUSION_POSTCODES:
                is_metro = True
            elif any(m in loc_str.lower() for m in ["sydney", "melbourne", "brisbane", "perth", "adelaide"]):
                is_metro = True
                
            # Table 1: Northern Australia only for hospitality
            hosp_in_north = False
            if is_hospitality:
                if postcode:
                    str_p = f"{postcode:04d}"
                    hosp_in_north = any(str_p.startswith(pref) for pref in NORTHERN_REMOTE_POSTCODE_PREFIXES)
                else:
                    hosp_in_north = any(nr in loc_str.lower() for nr in ["cairns", "port douglas", "whitsunday", "airlie", "darwin", "broome", "exmouth", "townsville", "kununurra"])
                    
            if has_blocker:
                job_status = "DISQUALIFIED"
                visa_fit = "blocked_citizenship"
                match_score = 1.0
                category = "Other"
                new_whv_blocked.append({"title": title, "employer": employer, "reason": blocker_reason, "url": canonical_url})
            elif has_op_disq:
                job_status = "DISQUALIFIED"
                visa_fit = "blocked_equipment_or_rsa"
                match_score = 1.5
                category = "Other"
                new_whv_blocked.append({"title": title, "employer": employer, "reason": op_disq_reason, "url": canonical_url})
            elif not (is_agro or is_solar or (is_hospitality and hosp_in_north)):
                job_status = "DISQUALIFIED"
                visa_fit = "not_462_specified_work"
                match_score = 1.8
                category = "Other"
                reason_detail = "Hospitalidade fora do Norte da Austrália" if is_hospitality else "Setor não elegível para 88 dias WHV 462"
                new_whv_blocked.append({"title": title, "employer": employer, "reason": reason_detail, "url": canonical_url})
            elif is_metro and not is_solar:
                job_status = "DISQUALIFIED"
                visa_fit = "blocked_metro_postcode"
                match_score = 1.8
                category = "Other"
                new_whv_blocked.append({"title": title, "employer": employer, "reason": f"Postcode {postcode} em área metropolitana", "url": canonical_url})
            else:
                job_status = "QUALIFIED"
                visa_fit = "88d_eligible"
                match_score = 4.9
                category = "88 Days Specified Work" if (is_agro or is_solar) else "Hospitality / Barista"
                new_whv_qualified.append({
                    "id": raw_id, "title": title, "employer": employer,
                    "loc": loc_str, "category": category, "postcode": postcode, "url": canonical_url
                })
                
        role_track = "sap" if track == "sap" else "operational"
        
        cur.execute("""
            INSERT INTO jobs (
                id, source, external_id, canonical_url, employer,
                title, location, description, publication_date,
                discovered_at, status, content_hash
            ) VALUES (?, 'seek', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                canonical_url=excluded.canonical_url,
                employer=excluded.employer,
                title=excluded.title,
                location=excluded.location,
                description=excluded.description,
                status=excluded.status,
                content_hash=excluded.content_hash;
        """, (
            job_id, raw_id, canonical_url, employer,
            title, loc_str, full_text, pub_date,
            now_iso, job_status, content_hash
        ))
        
        cur.execute("""
            INSERT INTO applications (
                id, job_id, profile_id, role_track, match_score,
                confidence, status, category, visa_fit, created_at, updated_at
            ) VALUES (?, ?, 'default_profile', ?, ?, 0.98, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                role_track=excluded.role_track,
                match_score=excluded.match_score,
                status=excluded.status,
                category=excluded.category,
                visa_fit=excluded.visa_fit,
                updated_at=excluded.updated_at;
        """, (
            app_id, job_id, role_track, match_score, job_status,
            category, visa_fit, now_iso, now_iso
        ))
        
        if job_status == "QUALIFIED":
            pipeline_additions.append(f"- [ ] {canonical_url} | {employer} | {title} | {loc_str} | posted: {pub_date[:10]}\n")
            history_additions.append(f"{now_iso}\t{canonical_url}\tqualified\t{employer}\t{title}\n")
            
    conn.commit()
    conn.close()
    
    if pipeline_additions:
        with open(PIPELINE_PATH, "a", encoding="utf-8") as f:
            for line in pipeline_additions:
                f.write(line)
                
    if history_additions:
        with open(SCAN_HISTORY_PATH, "a", encoding="utf-8") as f:
            for line in history_additions:
                f.write(line)
                
    print("\n======================================================================")
    print("                    RESULTADOS DA VARREDURA ATIVA                      ")
    print("======================================================================")
    print(f"Total de Vagas Brutas Analisadas (48h) : {scanned_count}")
    print(f"Já Existentes no Banco (Deduplicadas) : {already_tracked}")
    print(f"Novas Vagas SAP Qualificadas (Elegíveis): {len(new_sap_qualified)}")
    print(f"Vagas SAP Bloqueadas / Não-Especialistas: {len(new_sap_blocked)}")
    print(f"Novas Vagas WHV Qualificadas (88 Dias) : {len(new_whv_qualified)}")
    print(f"Vagas WHV Bloqueadas (Metro/Fora/Eq.): {len(new_whv_blocked)}")
    print("======================================================================\n")
    
    if new_sap_qualified:
        print("💻 TOP NOVAS VAGAS SAP ELEGÍVEIS:")
        for s in new_sap_qualified:
            print(f"  • {s['title']} @ {s['employer']} ({s['loc']})")
            print(f"    URL: {s['url']}")
            
    if new_whv_qualified:
        print("\n🌾 TOP NOVAS VAGAS WHV 88 DIAS ELEGÍVEIS:")
        for w in new_whv_qualified:
            print(f"  • {w['title']} @ {w['employer']} ({w['loc']}) — Postcode: {w.get('postcode') or 'Regional'}")
            print(f"    URL: {w['url']}")
            
    # Sincronização com o Notion
    print("\n======================================================================")
    print("                SINCRONIZAÇÃO EM TEMPO REAL COM O NOTION              ")
    print("======================================================================")
    try:
        adapter = NotionAdapter()
        report = adapter.sync_all_applications(db_path=DB_PATH, limit=60)
        print(f"Notion - Total de Candidaturas Analisadas : {report.get('total_candidates', 0)}")
        print(f"Notion - Sincronizadas / Criadas com Sucesso : {report.get('synced', 0)}")
        print(f"Notion - Falhas ou Ignoradas             : {report.get('failed', 0)}")
        print(f"Notion - Widgets do Dashboard Atualizados: {report.get('widgets_updated', 0)}")
    except Exception as e:
        print(f"Erro na sincronização com o Notion: {e}")

if __name__ == "__main__":
    main()
