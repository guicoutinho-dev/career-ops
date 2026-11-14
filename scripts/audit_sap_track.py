import sys
import sqlite3
from pathlib import Path

REPO_ROOT = Path("D:/AntigravityProjetos/career-ops")
sys.path.insert(0, str(REPO_ROOT))

from src.services.env import load_env
load_env(REPO_ROOT / ".env")
from src.integrations.notion.adapter import NotionAdapter

conn = sqlite3.connect(REPO_ROOT / "data/applications.db")
c = conn.cursor()

print("==================================================")
print("     AUDITORIA DA TRILHA SAP (role_track='sap')   ")
print("==================================================")

# 1. Distribution of SAP roles by status
c.execute("""
    SELECT status, count(id), sum(case when notion_page_id is not null and notion_page_id != '' then 1 else 0 end)
    FROM applications
    WHERE role_track = 'sap'
    GROUP BY status
    ORDER BY count(id) DESC
""")
sap_dist = c.fetchall()
print("Distribuição das Vagas SAP por Status:")
for st, total, synced in sap_dist:
    st_clean = str(st).encode('ascii', 'replace').decode('ascii')
    print(f"  • {st_clean:<32} Total: {total:>3} | No Notion: {synced:>3}")

# 2. Detailed list of APPLIED / 4. APLICADO SAP
print("\n--- VAGAS SAP JÁ APLICADAS (APPLIED / 4. APLICADO) ---")
c.execute("""
    SELECT a.id, j.employer, j.title, a.status, a.match_score, a.submitted_at, a.notion_page_id
    FROM applications a
    JOIN jobs j ON a.job_id = j.id
    WHERE a.role_track = 'sap' 
      AND a.status IN ('APPLIED', '4. APLICADO (Candidatura Enviada)')
    ORDER BY COALESCE(a.match_score, 0) DESC
""")
applied_sap = c.fetchall()
print(f"Total de Vagas SAP Aplicadas: {len(applied_sap)}")
for r in applied_sap:
    print(f"  • [{r[0]}] {r[1]} - {r[2]} | Score: {r[4]} | Status: {r[3]} | Notion: {'SIM' if r[6] else 'NAO'}")

# 3. Detailed list of QUALIFIED / Evaluated / 1. Triagem
print("\n--- VAGAS SAP EM QUALIFIED / Evaluated / 1. Triagem ---")
c.execute("""
    SELECT a.id, j.employer, j.title, a.status, a.match_score, a.notion_page_id
    FROM applications a
    JOIN jobs j ON a.job_id = j.id
    WHERE a.role_track = 'sap' 
      AND a.status IN ('QUALIFIED', 'Evaluated', '1. Triagem (Para Avaliar)', 'APPROVED')
    ORDER BY COALESCE(a.match_score, 0) DESC
""")
pipeline_sap = c.fetchall()
print(f"Total de Vagas SAP em Triagem/Qualificadas: {len(pipeline_sap)}")

unsynced_top_sap = []
for r in pipeline_sap:
    synced_txt = 'SIM' if r[5] else 'NAO'
    score_val = r[4] or 0.0
    print(f"  • [{r[0]}] {r[1]} - {r[2]} | Score: {score_val} | Status: {r[3]} | Notion: {synced_txt}")
    # Flag high score unsynced SAP vacancies
    if not r[5] and (score_val >= 80 or any(k in r[2].lower() for k in ['abap', 'btp', 'fiori', 'integration', 's/4'])):
        unsynced_top_sap.append(r[0])

print(f"\nTop vagas SAP qualificadas pendentes de sincronizacao no Notion: {len(unsynced_top_sap)}")

# 4. Sincronizar top vagas SAP qualificadas no Notion se houver alguma pendente
if unsynced_top_sap:
    print(f"Sincronizando {len(unsynced_top_sap)} top vagas SAP no Notion...")
    adapter = NotionAdapter()
    for app_id in unsynced_top_sap:
        ok, res = adapter.sync_application(app_id)
        print(f"  - Sincronizado {app_id}: ok={ok} page={res}")

conn.close()
