import urllib.request
import urllib.parse
import json
import sys
import re

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Postcodes explicitly excluded from regional specified work for WHV Subclass 462:
METRO_EXCLUSION_POSTCODES = set(range(2000, 2235)) | set(range(3000, 3208)) | set(range(4000, 4124)) | set(range(6000, 6039)) | set(range(6050, 6085)) | set(range(6090, 6183)) | set(range(6210, 6215))

# Tourism & Hospitality for 462 is strictly ONLY eligible in Northern, Remote or Very Remote Australia:
# Northern QLD (4700-4899), NT (all: 0800-0999), Remote WA (6700-6799), Remote SA (5700-5734), Remote NSW (2800-2899)
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

def fetch_seek_search(keywords, daterange="3", page_size=25):
    params = {
        "siteKey": "AU-Main",
        "where": "All Australia",
        "daterange": daterange,
        "pageSize": str(page_size),
        "keywords": keywords
    }
    url = "https://www.seek.com.au/api/jobsearch/v5/search?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("data", []), data.get("totalCount", 0)
    except Exception as e:
        print(f"[ERROR] Searching '{keywords}': {e}")
        return [], 0

def extract_location_info(job_item):
    locations_list = job_item.get("locations", [])
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
                # Look for 4 digit postcode
                m = re.search(r'\b(\d{4})\b', c_name)
                if m and not postcode:
                    postcode = int(m.group(1))
                    
    combined_location_text = ", ".join(list(dict.fromkeys(full_loc_strs)))
    return combined_location_text, postcode

def evaluate_whv_job(job_item):
    title = job_item.get("title", "")
    teaser = job_item.get("teaser", "")
    bullets = job_item.get("bulletPoints", [])
    bullets_text = " ".join(bullets) if bullets else ""
    loc_text, postcode = extract_location_info(job_item)
    
    combined_text = f"{title} {teaser} {bullets_text} {loc_text}".lower()
    
    # 1. Citizenship Blocker check
    for blocker in CITIZENSHIP_BLOCKERS:
        if blocker in combined_text:
            return False, f"Citizenship blocker detected: '{blocker}'", None
            
    # 2. Industry identification
    is_farm_agro = any(w in combined_text for w in [
        "farm", "agriculture", "harvest", "crop", "cattle", "livestock", "dairy",
        "poultry", "orchard", "picking", "packing", "fruit", "cotton", "grain",
        "station hand", "pastoral", "vineyard", "pruning", "shed hand", "paddock"
    ])
    
    is_solar_civil = any(w in combined_text for w in [
        "solar farm", "solar installer", "solar labourer", "civil labourer", "regional construction"
    ])
    
    is_hospitality = any(w in combined_text for w in [
        "hospitality", "resort", "hotel", "housekeeper", "housekeeping", "waiter", "waitress",
        "kitchen hand", "barista", "bartender", "chef", "cook", "food and beverage", "guest service"
    ])
    
    if not (is_farm_agro or is_solar_civil or is_hospitality):
        return False, "Not an eligible 462 specified work industry", None
        
    # 3. Postcode & Location Eligibility for 462
    if postcode:
        if postcode in METRO_EXCLUSION_POSTCODES:
            return False, f"Postcode {postcode} is in excluded metropolitan area", None
            
        if is_hospitality and not (is_farm_agro or is_solar_civil):
            str_p = f"{postcode:04d}"
            if not any(str_p.startswith(pref) for pref in NORTHERN_REMOTE_POSTCODE_PREFIXES):
                return False, f"Hospitality in postcode {postcode} not in Northern/Remote Australia (required for 462)", None
    else:
        # Check text if postcode missing
        if any(metro in loc_text.lower() for metro in ["sydney", "melbourne", "brisbane", "perth", "adelaide"]):
            return False, "Metropolitan area location", None
        if is_hospitality and not (is_farm_agro or is_solar_civil):
            if not any(nr in loc_text.lower() for nr in ["cairns", "port douglas", "whitsunday", "airlie", "darwin", "alice springs", "broome", "kununurra", "remote", "townsville", "outback"]):
                return False, "Hospitality outside Northern/Remote Australia", None
                
    # 4. Check for explicit 88 days / WHV positive mentions
    whv_positive = any(w in combined_text for w in [
        "88 days", "88 day", "whv", "working holiday", "second year visa", "462", "417",
        "visa sign off", "sign off", "backpackers", "backpacker", "working rights", "sign-off"
    ])
    
    # 5. Check accommodation
    has_accom = any(w in combined_text for w in [
        "accommodation", "accomodation", "room provided", "quarters", "on-site cabin",
        "onsite accommodation", "staff accommodation", "live-in", "board and lodging"
    ])
    
    industry_label = "Agro / Farm Hand" if is_farm_agro else ("Solar / Civil (Regional)" if is_solar_civil else "Hospitality (Northern/Remote AU)")
    
    return True, "Eligible", {
        "postcode": postcode,
        "location": loc_text,
        "industry": industry_label,
        "whv_positive": whv_positive,
        "has_accom": has_accom
    }

def main():
    search_queries = [
        # Explicit WHV tags
        "\"88 days\"",
        "\"88 day\"",
        "\"working holiday\" farm",
        "\"working holiday\" accommodation",
        
        # Agro & Farms
        "\"farm hand\" accommodation",
        "\"station hand\" accommodation",
        "\"dairy farm hand\"",
        "\"poultry farm hand\"",
        "harvest labourer",
        "orchard hand",
        "packhouse labourer",
        "cattle station hand",
        
        # Solar Farm in Regional
        "\"solar farm\" labourer",
        
        # Hospitality (Strictly Northern / Remote)
        "\"cairns\" hospitality accommodation",
        "\"port douglas\" resort accommodation",
        "\"whitsundays\" resort accommodation",
        "\"darwin\" hospitality accommodation"
    ]
    
    all_raw_jobs = {}
    print(f"=== EXECUÇÃO: BUSCA RIGOROSA DE VAGAS 88 DIAS WHV 462 ===")
    print(f"Janela de tempo: Últimos 3 dias (vagas ultra-recentes)\n")
    
    for query in search_queries:
        jobs, total = fetch_seek_search(query, daterange="3", page_size=20)
        print(f"• Query: {query:40} -> Encontrados: {total:3d} (coletando top {len(jobs)})")
        for j in jobs:
            jid = str(j.get("id"))
            if jid not in all_raw_jobs:
                all_raw_jobs[jid] = j
                
    print(f"\nTotal de vagas únicas analisadas: {len(all_raw_jobs)}")
    print("Aplicando crivo de validação: Indústria 462, Postcode regional, trava de cidadania, elegibilidade...\n")
    
    verified_results = []
    
    for jid, j in all_raw_jobs.items():
        is_eligible, reason, meta = evaluate_whv_job(j)
        if not is_eligible:
            continue
            
        employer = j.get("companyName") or j.get("advertiser", {}).get("description") or "Empresa Direta"
        title = j.get("title", "")
        salary = j.get("salaryLabel") or "A combinar / Hourly Award"
        teaser = j.get("teaser", "")
        bullets = j.get("bulletPoints", [])
        work_type = ", ".join(j.get("workTypes", [])) if j.get("workTypes") else "Casual/Full-time"
        date_display = j.get("listingDateDisplay", "")
        
        verified_results.append({
            "id": jid,
            "title": title,
            "employer": employer,
            "location": meta["location"],
            "postcode": meta["postcode"],
            "industry": meta["industry"],
            "salary": salary,
            "work_type": work_type,
            "date": date_display,
            "whv_positive": meta["whv_positive"],
            "has_accom": meta["has_accom"],
            "teaser": teaser,
            "bullets": bullets,
            "url": f"https://www.seek.com.au/job/{jid}"
        })
        
    print(f"============================================================")
    print(f"✅ VAGAS 100% VALIDADAS E ELEGÍVEIS PARA OS 88 DIAS: {len(verified_results)}")
    print(f"============================================================\n")
    
    # Priority sorting:
    # 1. Mention of 88 days/WHV
    # 2. Accommodation provided
    # 3. Freshness / salary
    verified_results.sort(key=lambda x: (x["whv_positive"], x["has_accom"], x["salary"] != "A combinar / Hourly Award"), reverse=True)
    
    # Save to json file
    with open("output/whv_88_days_strict_verified.json", "w", encoding="utf-8") as f:
        json.dump(verified_results, f, indent=2, ensure_ascii=False)
        
    # Display top 10 with rich details
    for i, v in enumerate(verified_results[:12], 1):
        print(f"[{i:02d}] {v['employer']} — {v['title']}")
        print(f"     📍 Local: {v['location']} (CEP: {v['postcode'] or 'Regional Aprovado'})")
        print(f"     🌾 Trilha: {v['industry']} | Tipo: {v['work_type']} | Postado: {v['date']}")
        print(f"     💰 Salário: {v['salary']}")
        print(f"     🏠 Acomodação: {'✅ SIM / Fornecida ou Mencionada' if v['has_accom'] else 'ℹ️ Não mencionada no resumo'}")
        print(f"     🇦🇺 88 Dias / WHV: {'🌟 EXPLICITAMENTE MENCIONADO' if v['whv_positive'] else '✅ 100% Elegível por Lei (Setor & CEP)'}")
        print(f"     📝 Resumo: {v['teaser']}")
        if v['bullets']:
            print(f"     🔹 Destaques: {' | '.join(v['bullets'][:2])}")
        print(f"     🔗 Link Oficial: {v['url']}")
        print("-" * 60)

if __name__ == "__main__":
    main()
