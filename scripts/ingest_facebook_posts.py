"""
ingest_facebook_posts.py — loads output/facebook_posts.json (written by
scan-facebook-groups.mjs) into data/applications.db.

Facebook posts are noisy and unverified, so every post lands as NEEDS_REVIEW,
never QUALIFIED: a human confirms it is a real, current job before anything
else happens. Post text is untrusted data; it is stored, never acted on.

Usage:
  python scripts/ingest_facebook_posts.py            # ingest
  python scripts/ingest_facebook_posts.py --dry-run  # show what would be ingested
"""

import sys
import json
import hashlib
import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from src.database.connection import get_connection
from scan_regional_whv import check_citizenship_blocker, is_location_eligible_462

POSTS_PATH = PROJECT_ROOT / "output" / "facebook_posts.json"
PIPELINE_PATH = PROJECT_ROOT / "data" / "pipeline.md"
SCAN_HISTORY_PATH = PROJECT_ROOT / "data" / "scan-history.tsv"

HOSP_TERMS = ["hospitality", "resort", "hotel", "chef", "cook", "kitchen", "housekeep",
              "waiter", "barista", "bartender", "room attendant"]
FARM_TERMS = ["farm", "harvest", "picking", "picker", "packing", "packer", "orchard",
              "vineyard", "cattle", "station", "livestock", "dairy", "pruning"]


def guess_title(text: str) -> str:
    first = next((ln.strip() for ln in text.splitlines() if len(ln.strip()) > 8), text[:80])
    return first[:120]


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    if not POSTS_PATH.exists():
        print(f"Nada para ingerir: {POSTS_PATH} não existe. Rode antes: node scan-facebook-groups.mjs")
        return
    posts = json.loads(POSTS_PATH.read_text(encoding="utf-8"))

    conn = get_connection()
    cur = conn.cursor()
    existing = {r[0] for r in cur.execute("SELECT id FROM jobs WHERE source='facebook_group'")}
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    new_rows, skipped = [], {"citizenship": 0, "location_462": 0, "already": 0}
    for p in posts:
        job_id = p["id"]
        if job_id in existing:
            skipped["already"] += 1
            continue
        text = p.get("text", "")
        if check_citizenship_blocker(text):
            skipped["citizenship"] += 1
            continue
        t = text.lower()
        is_hosp = any(k in t for k in HOSP_TERMS) and not any(k in t for k in FARM_TERMS)
        loc_hint = p.get("postcode") or text[:600]
        if not is_location_eligible_462(str(loc_hint), is_hospitality=is_hosp):
            skipped["location_462"] += 1
            continue

        contacts = ", ".join(p.get("phones", []) + p.get("emails", [])) or "via post/Messenger"
        url = p.get("permalink") or p.get("group_url", "")
        desc = (f"{text}\n\n• Group: {p.get('group')}\n• Author: {p.get('author') or '?'}\n"
                f"• Contacts: {contacts}\n• Postcode: {p.get('postcode') or '?'}\n"
                f"• Keywords: {', '.join(p.get('keyword_hits', []))}")
        score = 3.5 + 0.3 * min(len(p.get("priority_boosts", [])), 3)
        new_rows.append((job_id, url, p.get("author") or p.get("group"), guess_title(text),
                         p.get("postcode") or "Regional AU (ver post)", desc, round(min(score, 4.5), 1),
                         p.get("scraped_at", now_iso)))

    print(f"Posts no arquivo: {len(posts)} | novos elegíveis: {len(new_rows)} | ignorados: {skipped}")
    if dry_run:
        for r in new_rows[:15]:
            print(f"- [{r[6]}] {r[3]} | {r[1]}")
        conn.close()
        return

    pipeline_lines, history_lines = [], []
    for job_id, url, employer, title, loc, desc, score, pub in new_rows:
        content_hash = hashlib.sha256(desc.encode("utf-8")).hexdigest()
        cur.execute("""
            INSERT INTO jobs (id, source, external_id, canonical_url, employer, title, location,
                              description, publication_date, discovered_at, status, content_hash)
            VALUES (?, 'facebook_group', ?, ?, ?, ?, ?, ?, ?, ?, 'NEEDS_REVIEW', ?)
            ON CONFLICT(id) DO NOTHING
        """, (job_id, job_id, url, employer, title, loc, desc[:3500], pub, now_iso, content_hash))
        cur.execute("""
            INSERT INTO applications (id, job_id, profile_id, role_track, match_score, confidence,
                                      status, category, visa_fit, created_at, updated_at)
            VALUES (?, ?, 'default_profile', 'operational', ?, 0.5, 'NEEDS_REVIEW',
                    '88 Days Specified Work', 'unverified_facebook', ?, ?)
            ON CONFLICT(id) DO NOTHING
        """, (f"app_{job_id}", job_id, score, now_iso, now_iso))
        pipeline_lines.append(f"- [ ] {url} | {employer} | {title[:80]} | {loc} | source: facebook (REVISAR)\n")
        history_lines.append(f"{now_iso}\t{url}\tneeds_review\t{employer}\t{title[:80]}\n")

    conn.commit()
    conn.close()
    if pipeline_lines:
        with open(PIPELINE_PATH, "a", encoding="utf-8") as f:
            f.writelines(pipeline_lines)
        with open(SCAN_HISTORY_PATH, "a", encoding="utf-8") as f:
            f.writelines(history_lines)
    print(f"✅ {len(new_rows)} posts ingeridos como NEEDS_REVIEW.")


if __name__ == "__main__":
    main()
