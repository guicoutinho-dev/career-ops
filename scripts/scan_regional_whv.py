"""
scan_regional_whv.py — Regional WHV 462 & 88 Days Scanner
Connects to:
1. Farmwork4u JSON REST API (https://api.farmwork4u.com/listings)
2. G'Day Backpacker Jobs (https://www.gdaybackpackerjobs.com.au/jobs)

Extracts structured roles, checks Subclass 462 regional eligibility,
calculates match scores, ingests into SQLite data/applications.db,
and updates data/pipeline.md and data/scan-history.tsv.
"""

import sys
import os
import re
import json
import hashlib
import datetime
import urllib.request
import urllib.parse
from pathlib import Path
from bs4 import BeautifulSoup

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

PROJECT_ROOT = Path(r"D:\AntigravityProjetos\career-ops")
DB_PATH = PROJECT_ROOT / "data" / "applications.db"
PIPELINE_PATH = PROJECT_ROOT / "data" / "pipeline.md"
SCAN_HISTORY_PATH = PROJECT_ROOT / "data" / "scan-history.tsv"
REPORT_PATH = PROJECT_ROOT / "reports" / "regional_whv_hunt_report.md"

sys.path.insert(0, str(PROJECT_ROOT))
from src.database.connection import get_connection

# Postcodes explicitly excluded from regional specified work for WHV Subclass 462
METRO_EXCLUSION_POSTCODES = set(range(2000, 2235)) | set(range(3000, 3208)) | set(range(4000, 4124)) | set(range(6000, 6039)) | set(range(6050, 6085)) | set(range(6090, 6183)) | set(range(6210, 6215))

# Tourism & Hospitality for 462 is strictly ONLY eligible in Northern, Remote or Very Remote Australia:
NORTHERN_REMOTE_POSTCODE_PREFIXES = ("08", "09", "47", "48", "67", "57", "28")

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
    "nv1", "nv2", "baseline clearance", "security clearance required"
]

def check_citizenship_blocker(text: str) -> bool:
    t = text.lower()
    return any(b in t for b in CITIZENSHIP_BLOCKERS)

def extract_postcode(loc_str: str) -> int | None:
    m = re.search(r'\b(\d{4})\b', loc_str)
    return int(m.group(1)) if m else None

def is_location_eligible_462(loc_str: str, is_hospitality: bool = False) -> bool:
    postcode = extract_postcode(loc_str)
    if postcode:
        if postcode in METRO_EXCLUSION_POSTCODES:
            return False
        if is_hospitality:
            str_p = f"{postcode:04d}"
            if not any(str_p.startswith(pref) for pref in NORTHERN_REMOTE_POSTCODE_PREFIXES):
                return False
        return True
    
    # Text-based check if no 4-digit postcode
    loc_lower = loc_str.lower()
    if any(m in loc_lower for m in ["sydney", "melbourne", "brisbane", "perth", "adelaide"]):
        return False
    if is_hospitality:
        return any(nr in loc_lower for nr in [
            "cairns", "port douglas", "whitsunday", "airlie", "darwin", "alice springs",
            "broome", "kununurra", "yulara", "ayers rock", "townsville", "remote", "outback"
        ])
    return True

# -------------------------------------------------------------
# 1. Fetch Farmwork4u
# -------------------------------------------------------------
def fetch_farmwork4u() -> list[dict]:
    print("[FARMWORK4U] Consultando API REST (https://api.farmwork4u.com/listings)...", flush=True)
    req = urllib.request.Request(
        "https://api.farmwork4u.com/listings",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            listings = data.get("listings", [])
            print(f"  -> {len(listings)} vagas encontradas na API Farmwork4u")
            
            jobs = []
            for l in listings:
                lid = l.get("id")
                if not lid:
                    continue
                role = l.get("Role Name") or "Farm Hand"
                business = l.get("Business Name") or "Direct Employer"
                location = f"{l.get('Location', 'Regional')}, {l.get('State', 'AU')}"
                pay = l.get("Pay Rate") or "Award Rate"
                start = l.get("Start Date") or "Immediate / Flexible"
                end = l.get("End Date") or ""
                desc = l.get("Role Description") or ""
                accom = l.get("Accommodation") or "None"
                work_types = ", ".join(l.get("Work Types", [])) if isinstance(l.get("Work Types"), list) else ""
                visas = l.get("Visa Eligibility", [])
                
                full_desc = (
                    f"{desc}\n\n"
                    f"• Business: {business}\n"
                    f"• Pay Rate: {pay}\n"
                    f"• Start Date: {start} | End Date: {end}\n"
                    f"• Accommodation: {accom}\n"
                    f"• Work Types: {work_types}\n"
                    f"• Hours/Week: {l.get('Hours Per Week', 'Standard')}\n"
                    f"• Car Required: {'Yes' if l.get('Car Required') else 'No'}\n"
                    f"• Visa Eligibility: {visas}"
                )
                
                jobs.append({
                    "id": f"fw4u_{lid}",
                    "source": "farmwork4u",
                    "external_id": lid,
                    "canonical_url": f"https://farmwork4u.com/listing.html?id={lid}",
                    "employer": business,
                    "title": role,
                    "location": location,
                    "description": full_desc,
                    "publication_date": l.get("Date Listed") or datetime.date.today().isoformat(),
                    "accommodation": accom,
                    "start_date": start,
                    "pay_rate": pay,
                    "industry": "Agro / Horticulture (88 Days)",
                    "whv_positive": "462" in visas,
                    "has_accom": True if accom and accom.lower() not in ["no", "none"] else False
                })
            return jobs
    except Exception as e:
        print(f"  [ERROR] Falha ao consultar Farmwork4u: {e}")
        return []

# -------------------------------------------------------------
# 2. Fetch G'Day Backpacker Jobs
# -------------------------------------------------------------
def fetch_gday_backpacker_jobs() -> list[dict]:
    print("[GDAY BACKPACKER] Coletando vagas em gdaybackpackerjobs.com.au...", flush=True)
    base_url = "https://www.gdaybackpackerjobs.com.au"
    categories = [
        "/jobs",
        "/jobs?category=farm-work",
        "/jobs?category=fruit-picking",
        "/jobs?category=hospitality"
    ]
    
    unique_links = set()
    for cat in categories:
        try:
            req = urllib.request.Request(f"{base_url}{cat}", headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                soup = BeautifulSoup(html, "html.parser")
                for a in soup.find_all("a"):
                    href = a.get("href", "")
                    if href.startswith("/jobs/cm"):
                        unique_links.add(href)
        except Exception as e:
            print(f"  [WARN] Falha ao varrer categoria {cat}: {e}")
            
    print(f"  -> {len(unique_links)} links únicos de vagas encontrados")
    
    jobs = []
    for href in list(unique_links):
        job_url = f"{base_url}{href}"
        jid = href.split("/")[-1]
        try:
            req = urllib.request.Request(job_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                soup = BeautifulSoup(html, "html.parser")
                
                h1 = soup.find("h1")
                title = h1.get_text(strip=True) if h1 else "Regional Backpacker Role"
                
                # Clean emoji spam if present in title
                clean_title = re.sub(r'[\U00010000-\U0010ffff]', '', title).strip()
                if not clean_title:
                    clean_title = title
                    
                # Employer
                employer = "Regional Employer"
                # The employer logo lives under /org-logos/; the first <img> on the
                # page is the site's own logo, so never fall back to it.
                img = soup.find("img", src=re.compile(r"org-logos"), alt=True)
                if img and img.get("alt") and len(img["alt"].strip()) > 2:
                    employer = img["alt"].strip()
                else:
                    # Look for company name near title or metadata
                    txt = soup.get_text(separator="\n", strip=True)
                    m_comp = re.search(r'Posted\s+\d+\s+\w+\s+\d{4}\s*\n+([^\n]+)', txt)
                    if m_comp:
                        employer = m_comp.group(1).strip()
                        
                # Location & Postcode
                loc_match = re.search(r'([A-Za-z\s]+),\s*(Queensland|New South Wales|Victoria|Western Australia|South Australia|Northern Territory|Tasmania|QLD|NSW|VIC|WA|SA|NT|TAS)\s*(\d{4})?', soup.get_text())
                location = loc_match.group(0).strip() if loc_match else "Regional Australia"
                if employer != "Regional Employer" and location.startswith(employer):
                    location = location[len(employer):].strip() or location
                
                # Check 88 days badge
                whv_88_badge = "88 days valid" in html.lower()
                has_accom = "accommodation provided" in html.lower() or "accommodation options" in html.lower()
                
                # Pay rate
                pay_match = re.search(r'\$[\d\.]+\s*(?:–\s*\$[\d\.]+)?\s*/\s*(?:hourly|hour|hr|weekly|week)', html, re.I)
                pay_rate = pay_match.group(0) if pay_match else "Award Rate"
                
                # Full body description
                desc_div = soup.find("main") or soup.body
                desc_text = desc_div.get_text(separator="\n", strip=True) if desc_div else ""
                
                jobs.append({
                    "id": f"gday_{jid}",
                    "source": "gdaybackpackerjobs",
                    "external_id": jid,
                    "canonical_url": job_url,
                    "employer": employer,
                    "title": clean_title,
                    "location": location,
                    "description": desc_text[:3500],
                    "publication_date": datetime.date.today().isoformat(),
                    "accommodation": "Provided / Available" if has_accom else "Self-sourced / Ask",
                    "start_date": "Immediate / As indicated",
                    "pay_rate": pay_rate,
                    "industry": "Regional Hospitality / Agro" if "hospitality" in href else "Agro / Farm Work",
                    "whv_positive": whv_88_badge,
                    "has_accom": has_accom
                })
        except Exception as e:
            continue
            
    print(f"  -> {len(jobs)} vagas parseadas com sucesso do G'Day Backpacker Jobs")
    return jobs

# -------------------------------------------------------------
# 3. Fetch Workforce Australia — Harvest Trail Official API
# -------------------------------------------------------------
def fetch_harvest_trail() -> list[dict]:
    url = "https://www.workforceaustralia.gov.au/api/v1/global/vacancies?isHarvestJob=true&pageSize=50"
    print(f"[HARVEST TRAIL / WORKFORCE AU] Consultando API oficial ({url})...", flush=True)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            items = data.get("results", [])
            print(f"  -> {len(items)} vagas de safra oficiais retornadas pelo Harvest Trail")
            jobs = []
            for item in items:
                r = item.get("result") or item
                vid = r.get("vacancyId")
                if not vid:
                    continue
                title = (r.get("title") or "Harvest Worker").strip()
                employer = (r.get("employerName") or "Harvest Trail Employer").strip()
                suburb = (r.get("suburb") or "").strip().title()
                state = (r.get("state") or "").strip()
                postcode = (r.get("postCode") or "").strip()
                region_label = (r.get("location") or {}).get("label", "")
                location = f"{suburb}, {state} {postcode}".strip(", ") or region_label or "Regional Australia"
                desc = (r.get("description") or "").strip()
                salary = (r.get("salary") or {}).get("label") or "Horticulture Award"
                positions = r.get("positionsAvailable") or 1
                created = (r.get("creationDate") or datetime.date.today().isoformat())[:10]
                has_accom = any(w in desc.lower() for w in ["accommodation", "accomodation", "on-site", "onsite", "hostel", "camp"])

                full_desc = (
                    f"{desc}\n\n"
                    f"• Official Workforce Australia / Harvest Trail Vacancy ID: {vid}\n"
                    f"• Employer: {employer}\n"
                    f"• Region: {region_label} ({location})\n"
                    f"• Positions Available: {positions}\n"
                    f"• Pay / Award: {salary}"
                )
                jobs.append({
                    "id": f"ht_{vid}",
                    "source": "harvest_trail",
                    "external_id": str(vid),
                    "canonical_url": f"https://www.workforceaustralia.gov.au/individuals/jobs/details/{vid}",
                    "employer": employer,
                    "title": f"{title} ({positions} vagas)" if positions and int(positions) > 1 else title,
                    "location": location,
                    "description": full_desc,
                    "publication_date": created,
                    "accommodation": "Mentioned in JD" if has_accom else "Check with Harvest Trail",
                    "start_date": "Seasonal / As per JD",
                    "pay_rate": salary,
                    "industry": "Agro / Harvest Trail (Gov AU)",
                    "whv_positive": True,  # Official Harvest Trail flag
                    "has_accom": has_accom,
                })
            return jobs
    except Exception as e:
        print(f"  [WARN] Harvest Trail API indisponível no momento ({e})")
        return []

# -------------------------------------------------------------
# 4. Fetch Backpacker Job Board — Farm Work & Fruit Picking
# -------------------------------------------------------------
def fetch_backpacker_job_board() -> list[dict]:
    print("[BACKPACKER JOB BOARD] Coletando vagas recentes de farm-work e fruit-picking...", flush=True)
    urls = [
        "https://www.backpackerjobboard.com.au/jobs/farm-work/",
    ]
    seen_ids = set()
    jobs = []
    for u in urls:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                soup = BeautifulSoup(html, "html.parser")
                for card in soup.find_all("div", class_="job-entry"):
                    a = card.select_one("h3.job-title a[href]")
                    if not a:
                        continue
                    href = a["href"].strip()
                    m_id = re.search(r"/job/(\d+)/", href)
                    if not m_id:
                        continue
                    jid = m_id.group(1)
                    if jid in seen_ids:
                        continue
                    pub = (card.select_one("p.job-published") or card).get_text(strip=True)
                    # Keep only fresh listings (hours ago or <= 14 days ago)
                    m_days = re.search(r"(\d+)d\s+ago", pub)
                    if m_days and int(m_days.group(1)) > 14:
                        continue
                    if not ("hr" in pub or "min" in pub or m_days):
                        continue
                    seen_ids.add(jid)
                    title = a.get_text(strip=True)
                    comp_el = card.select_one("p.job-company")
                    employer = comp_el.get_text(" ", strip=True) if comp_el else "Regional Employer"
                    loc_el = card.select_one("p.job-location")
                    location = re.sub(r"\s*,\s*", ", ", loc_el.get_text(" ", strip=True)) if loc_el else "Regional Australia"
                    desc_el = card.select_one("p.job-description")
                    snippet = desc_el.get_text(" ", strip=True) if desc_el else ""
                    has_accom = any(w in (title + " " + snippet).lower() for w in ["accom", "live-in", "housing", "quarters"])
                    whv_pos = any(w in (title + " " + snippet).lower() for w in ["88 day", "whv", "462", "417", "working holiday", "second year"])
                    jobs.append({
                        "id": f"bjb_{jid}",
                        "source": "backpackerjobboard",
                        "external_id": jid,
                        "canonical_url": href,
                        "employer": employer,
                        "title": title,
                        "location": location,
                        "description": f"{snippet}\n\n• Published: {pub}\n• Source: {href}",
                        "publication_date": datetime.date.today().isoformat(),
                        "accommodation": "Mentioned" if has_accom else "See listing",
                        "start_date": f"Recent ({pub})",
                        "pay_rate": "Award / See listing",
                        "industry": "Agro / Farm Work",
                        "whv_positive": whv_pos,
                        "has_accom": has_accom,
                    })
        except Exception as e:
            print(f"  [WARN] Falha em {u}: {e}")
    print(f"  -> {len(jobs)} vagas recentes (<=14d) coletadas do Backpacker Job Board")
    return jobs

# -------------------------------------------------------------
# Main Ingestion Protocol
# -------------------------------------------------------------
def main():
    print("=============================================================")
    print("  CAÇADA REGIONAL: FARMWORK4U + G'DAY + HARVEST TRAIL + BJB  ")
    print("=============================================================\n")
    
    fw_jobs = fetch_farmwork4u()
    gday_jobs = fetch_gday_backpacker_jobs()
    ht_jobs = fetch_harvest_trail()
    bjb_jobs = fetch_backpacker_job_board()
    all_raw_jobs = fw_jobs + gday_jobs + ht_jobs + bjb_jobs
    
    print(f"\n[TOTAL BRUTO] {len(all_raw_jobs)} vagas coletadas de ambas as fontes.")
    print("Aplicando crivo de elegibilidade Subclass 462 & 88 dias...")
    
    conn = get_connection()
    cur = conn.cursor()
    
    # Load existing applied to prevent duplicates
    applied_urls = set()
    cur.execute("""
        SELECT j.canonical_url FROM jobs j
        LEFT JOIN applications a ON a.job_id = j.id
        WHERE j.status IN ('APPLIED', '4. APLICADO', 'INTERVIEW')
           OR a.status IN ('APPLIED', '4. APLICADO', 'INTERVIEW', 'NEEDS_REVIEW')
    """)
    for r in cur.fetchall():
        if r[0]:
            applied_urls.add(r[0].lower().strip())
            
    existing_job_ids = set()
    cur.execute("SELECT id FROM jobs")
    for r in cur.fetchall():
        if r[0]:
            existing_job_ids.add(r[0])
            
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    today_str = datetime.date.today().isoformat()
    
    qualified_list = []
    skipped_count = 0
    
    pipeline_lines = []
    history_lines = []
    
    for j in all_raw_jobs:
        url = j["canonical_url"]
        if url.lower().strip() in applied_urls:
            skipped_count += 1
            continue
            
        full_text = f"{j['title']} {j['employer']} {j['location']} {j['description']}"
        if check_citizenship_blocker(full_text):
            continue
            
        # 462 specified-work industry check. Classify on the title first: board
        # descriptions mention "farm work" / "88 days" generically, which made a
        # Tasmanian chef or a Geelong event crew look like farm work.
        title_l = j["title"].lower()
        text_l = full_text.lower()
        farm_terms = ["farm", "picker", "picking", "packer", "packing", "harvest", "orchard",
                      "vineyard", "cattle", "station", "livestock", "dairy", "milker", "stable",
                      "horse", "fruit", "veg", "mango", "pruning", "crop", "forklift"]
        hosp_terms = ["chef", "cook", "kitchen", "waiter", "waitress", "barista", "bartender",
                      "housekeep", "room attendant", "front office", "guest service", "hospitality",
                      "resort", "hotel", "bar staff", "food and beverage", "f&b"]
        build_terms = ["construction", "trades assistant", "labourer", "solar"]
        is_farm = any(t in title_l for t in farm_terms)
        is_hosp = not is_farm and any(t in title_l for t in hosp_terms)
        is_build = not is_farm and not is_hosp and any(t in title_l for t in build_terms) \
            and not any(t in title_l for t in ["event", "warehouse", "retail"])
        if not (is_farm or is_hosp or is_build):
            # Title is vague (e.g. "SOS! 5 backpacker legends needed"): fall back
            # to the body, but only for farm work, which counts in all regional AU.
            if any(t in text_l for t in farm_terms) and not any(t in title_l for t in ["event", "au pair"]):
                is_farm = True
            else:
                continue
        if not is_location_eligible_462(j["location"], is_hospitality=is_hosp):
            continue
            
        # Match score calculation
        score = 4.0
        if j.get("whv_positive"):
            score += 0.4
        if j.get("has_accom"):
            score += 0.4
        if any(term in j["start_date"].lower() for term in ["late nov", "end of nov", "december", "mid nov", "november"]):
            score += 0.2
        final_score = min(5.0, round(score, 1))
        
        job_id = j["id"]
        app_id = f"app_{job_id}"
        content_hash = hashlib.sha256(j["description"].encode("utf-8")).hexdigest()
        
        # Upsert jobs table
        cur.execute("""
            INSERT INTO jobs (
                id, source, external_id, canonical_url, employer,
                title, location, description, publication_date,
                discovered_at, status, content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'QUALIFIED', ?)
            ON CONFLICT(id) DO UPDATE SET
                canonical_url=excluded.canonical_url,
                employer=excluded.employer,
                title=excluded.title,
                location=excluded.location,
                description=excluded.description,
                status=CASE WHEN jobs.status IN ('APPLIED', '4. APLICADO', 'INTERVIEW')
                            THEN jobs.status ELSE 'QUALIFIED' END,
                content_hash=excluded.content_hash,
                last_verified_at=excluded.discovered_at;
        """, (
            job_id, j["source"], j["external_id"], url, j["employer"],
            j["title"], j["location"], j["description"][:3500], j["publication_date"],
            now_iso, content_hash
        ))
        
        # Upsert applications table
        cur.execute("""
            INSERT INTO applications (
                id, job_id, profile_id, role_track, match_score,
                confidence, status, category, visa_fit, created_at, updated_at
            ) VALUES (?, ?, 'default_profile', 'operational', ?, 0.9, 'QUALIFIED', '88 Days Specified Work', 'eligible_whv_88days', ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                role_track='operational',
                match_score=excluded.match_score,
                confidence=0.9,
                status=CASE WHEN applications.status IN ('APPLIED', '4. APLICADO', 'INTERVIEW', 'NEEDS_REVIEW')
                            THEN applications.status ELSE 'QUALIFIED' END,
                category='88 Days Specified Work',
                visa_fit='eligible_whv_88days',
                updated_at=excluded.updated_at;
        """, (
            app_id, job_id, final_score, now_iso, now_iso
        ))
        
        # Only new jobs go to the pipeline inbox / scan history, so re-runs
        # refresh the DB rows without duplicating lines.
        if job_id not in existing_job_ids:
            pipeline_lines.append(f"- [ ] {url} | {j['employer']} | {j['title']} | {j['location']} | pay: {j['pay_rate']}\n")
            history_lines.append(f"{now_iso}\t{url}\tqualified\t{j['employer']}\t{j['title']}\n")
        
        j["match_score"] = final_score
        qualified_list.append(j)
        
    # Rows ingested by an earlier, looser run that no longer pass: demote them.
    # Only untouched QUALIFIED rows are changed, never anything already applied.
    kept_ids = {f"app_{q['id']}" for q in qualified_list}
    cur.execute("""
        SELECT a.id FROM applications a JOIN jobs j ON a.job_id = j.id
        WHERE j.source IN ('farmwork4u', 'gdaybackpackerjobs') AND a.status = 'QUALIFIED'
    """)
    stale = [r[0] for r in cur.fetchall() if r[0] not in kept_ids]
    for app_id in stale:
        cur.execute("UPDATE applications SET status='DISQUALIFIED', updated_at=? WHERE id=? AND status='QUALIFIED'",
                    (now_iso, app_id))
    if stale:
        print(f"[LIMPEZA] {len(stale)} vaga(s) de execuções anteriores rebaixadas para DISQUALIFIED: {stale}")

    conn.commit()
    conn.close()
    
    # Append to pipeline.md
    if pipeline_lines:
        with open(PIPELINE_PATH, "a", encoding="utf-8") as f:
            f.writelines(pipeline_lines)
            
    # Append to scan-history.tsv
    if history_lines:
        with open(SCAN_HISTORY_PATH, "a", encoding="utf-8") as f:
            f.writelines(history_lines)
            
    print(f"\n=============================================================")
    print(f"✅ VAGAS QUALIFICADAS INGERIDAS NO BANCO: {len(qualified_list)}")
    print(f"=============================================================\n")
    
    # Sort by match score
    qualified_list.sort(key=lambda x: x["match_score"], reverse=True)
    by_src = {}
    for q in qualified_list:
        by_src[q["source"]] = by_src.get(q["source"], 0) + 1
    src_summary = " · ".join(f"`{k}`: **{v}**" for k, v in by_src.items())
    
    # Generate Markdown Report
    report_content = [
        f"# 🌾 Relatório de Vagas Regionais WHV 462 (88 Dias)",
        f"> **Fontes:** Farmwork4u API · G'Day Backpacker Jobs · Harvest Trail (Workforce Australia Gov) · Backpacker Job Board",
        f"> **Data da Varredura:** {datetime.datetime.now().strftime('%d/%m/%Y %H:%M BRT')}",
        f"> **Total de Vagas Qualificadas:** **{len(qualified_list)}** ({src_summary})\n",
        f"---",
        f"## 🏆 Top Oportunidades em Destaque (Alta Aderência & Acomodação)\n"
    ]
    
    for i, q in enumerate(qualified_list[:25], 1):
        report_content.append(
            f"### {i}. {q['title']} — {q['employer']}\n"
            f"- **Fonte:** `{q['source']}` | **Match Score:** `{q['match_score']} / 5.0`\n"
            f"- **Localização:** 📍 {q['location']}\n"
            f"- **Remuneração:** 💰 {q['pay_rate']}\n"
            f"- **Acomodação:** 🏠 {'✅ Fornecida / Disponível' if q['has_accom'] else 'Informada no descritivo'}\n"
            f"- **Data de Início:** 📅 {q['start_date']}\n"
            f"- **Link Direto:** [Acessar Vaga]({q['canonical_url']})\n"
        )
        
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(report_content))
        
    print(f"Relatório gerado em: {REPORT_PATH}")

if __name__ == "__main__":
    main()
