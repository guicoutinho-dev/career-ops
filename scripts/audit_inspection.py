import os
import sys
import json
import sqlite3
import datetime
from pathlib import Path

REPO_ROOT = Path("D:/AntigravityProjetos/career-ops")
conn = sqlite3.connect(REPO_ROOT / "data/applications.db")
c = conn.cursor()

print("==================================================")
print("   PARTE 1: VARREDURA GMAIL & EMAIL_MESSAGES      ")
print("==================================================")

# 1. Interview invites
print("\n--- CONVITES DE ENTREVISTA ---")
rows = c.execute("SELECT id, sender, subject, received_at, matched_application_id FROM email_messages WHERE classification = 'INTERVIEW'").fetchall()
for r in rows:
    print(f"[{r[0]}] {r[3][:16]} | {r[1]} | {r[2]} | Matched: {r[4]}")

# 2. Recruiter direct contacts
print("\n--- RESPOSTAS DIRETAS / CONTATO DE RECRUTADOR ---")
rows = c.execute("SELECT id, sender, subject, received_at, matched_application_id FROM email_messages WHERE classification = 'RECRUITER_CONTACT'").fetchall()
for r in rows:
    print(f"[{r[0]}] {r[3][:16]} | {r[1]} | {r[2]} | Matched: {r[4]}")

# 3. Submissions
print("\n--- CONFIRMACOES DE SUBMISSAO (ACKNOWLEDGMENT) ---")
sources = {
    'SEEK': "sender LIKE '%seek%' OR subject LIKE '%SEEK%'",
    'Taleo / Hamilton Island': "sender LIKE '%taleo%' OR sender LIKE '%hamiltonisland%'",
    'Zapid': "sender LIKE '%zapid%' OR subject LIKE '%Zapid%'",
    'Backpacker Job Board': "sender LIKE '%backpacker%' OR subject LIKE '%Backpacker%'",
    'SmartRecruiters': "sender LIKE '%smartrecruiters%' OR subject LIKE '%SmartRecruiters%'",
    'GrainCorp ATS': "sender LIKE '%graincorp%'",
    'Outros Empregadores': "sender NOT LIKE '%seek%' AND sender NOT LIKE '%taleo%' AND sender NOT LIKE '%hamiltonisland%' AND sender NOT LIKE '%zapid%' AND sender NOT LIKE '%backpacker%' AND sender NOT LIKE '%smartrecruiters%' AND sender NOT LIKE '%graincorp%'"
}

total_ack = c.execute("SELECT count(id) FROM email_messages WHERE classification = 'ACKNOWLEDGMENT'").fetchone()[0]
print(f"Total de Acknowledgment emails: {total_ack}")
for name, cond in sources.items():
    cnt = c.execute(f"SELECT count(id) FROM email_messages WHERE classification = 'ACKNOWLEDGMENT' AND ({cond})").fetchone()[0]
    sample = c.execute(f"SELECT subject, received_at FROM email_messages WHERE classification = 'ACKNOWLEDGMENT' AND ({cond}) ORDER BY received_at DESC LIMIT 1").fetchone()
    sample_txt = f" (ex: '{sample[0][:40]}' em {sample[1][:10]})" if sample else ""
    print(f"  • {name}: {cnt} emails{sample_txt}")

print("\n==================================================")
print("   PARTE 2: RECONCILIACAO NOTION VS SQLITE        ")
print("==================================================")

statuses = ['INTERVIEW', 'APPLIED', '4. APLICADO (Candidatura Enviada)', 'CLOSED_LOST', 'APPROVED', 'Evaluated']
for st in statuses:
    tot = c.execute("SELECT count(id) FROM applications WHERE status = ?", (st,)).fetchone()[0]
    notion_cnt = c.execute("SELECT count(id) FROM applications WHERE status = ? AND notion_page_id IS NOT NULL AND notion_page_id != ''", (st,)).fetchone()[0]
    print(f"  • {st:<32} SQLite: {tot:>3} | Notion: {notion_cnt:>3} ({(notion_cnt/tot*100 if tot else 0):.0f}%)")

# Audit applied cards: check dates, score, links
print("\n--- AUDITORIA DE INTEGRIDADE DOS CARDS APLICADOS ---")
applied_rows = c.execute("""
    SELECT a.id, a.status, a.submitted_at, a.created_at, a.match_score, j.canonical_url, a.notion_page_id
    FROM applications a
    JOIN jobs j ON a.job_id = j.id
    WHERE a.status IN ('INTERVIEW', 'APPLIED', '4. APLICADO (Candidatura Enviada)')
""").fetchall()

missing_dates = []
missing_scores = []
missing_links = []
missing_notion = []

for r in applied_rows:
    app_id, st, sub_at, cr_at, score, url, n_id = r
    # date check: either submitted_at or created_at
    if not (sub_at or cr_at):
        missing_dates.append(app_id)
    if score is None:
        missing_scores.append(app_id)
    if not url or not url.startswith("http"):
        missing_links.append(app_id)
    if not n_id or len(n_id) < 10:
        missing_notion.append(app_id)

print(f"Total de cards aplicados/em entrevista auditados: {len(applied_rows)}")
print(f"  - Cards sem Data (submitted_at/created_at): {len(missing_dates)}")
if missing_dates:
    print(f"    IDs: {missing_dates}")
print(f"  - Cards sem Match Score: {len(missing_scores)}")
if missing_scores:
    print(f"    IDs: {missing_scores}")
print(f"  - Cards sem Link Canônico (Job URL): {len(missing_links)}")
if missing_links:
    print(f"    IDs: {missing_links}")
print(f"  - Cards sem Notion Page ID: {len(missing_notion)}")
if missing_notion:
    print(f"    IDs: {missing_notion}")

print("\n==================================================")
print("   PARTE 3: CADENCIA DE FOLLOW-UPS (7 DIAS UTEIS) ")
print("==================================================")
# Target date is 2026-11-03.
# 7 business days ago:
# Today: Tuesday 2026-11-03
# Day 1: Monday 2026-11-02
# Day 2: Friday 2026-10-30
# Day 3: Thursday 2026-10-29
# Day 4: Wednesday 2026-10-28
# Day 5: Tuesday 2026-10-27
# Day 6: Monday 2026-10-26
# Day 7: Friday 2026-10-23
# Any application submitted on or before Friday 2026-10-23 is past 7 business days!

def get_submission_date(sub_str, cr_str):
    s = sub_str or cr_str
    if not s:
        return None
    try:
        return datetime.date.fromisoformat(s[:10])
    except Exception:
        return None

# Business days calculator
current_date = datetime.date(2026, 11, 3)

def business_days_between(d1, d2):
    cur = d1 + datetime.timedelta(days=1)
    bd = 0
    while cur <= d2:
        if cur.weekday() < 5: # Mon-Fri
            bd += 1
        cur += datetime.timedelta(days=1)
    return bd

active_apps = c.execute("""
    SELECT a.id, j.employer, j.title, a.submitted_at, a.created_at, a.status, a.next_action_at
    FROM applications a
    JOIN jobs j ON a.job_id = j.id
    WHERE a.status IN ('APPLIED', '4. APLICADO (Candidatura Enviada)')
    ORDER BY COALESCE(a.submitted_at, a.created_at) ASC
""").fetchall()

due_for_followup = []
upcoming_followup = []

for app in active_apps:
    app_id, employer, title, sub_at, cr_at, st, next_act = app
    dt = get_submission_date(sub_at, cr_at)
    if dt:
        bdays = business_days_between(dt, current_date)
        if bdays >= 7:
            due_for_followup.append((app_id, employer, title, dt.isoformat(), bdays))
        else:
            upcoming_followup.append((app_id, employer, title, dt.isoformat(), bdays))

print(f"\nTotal de candidaturas ativas avaliadas para régua: {len(active_apps)}")
print(f"Candidaturas no prazo de cobrança (>= 7 dias úteis sem retorno): {len(due_for_followup)}")
print(f"Candidaturas dentro do prazo inicial (< 7 dias úteis): {len(upcoming_followup)}")

print("\n--- LISTA: CANDIDATURAS COM FOLLOW-UP DEVIDO (>= 7 DIAS ÚTEIS) ---")
for item in due_for_followup[:25]: # sample
    print(f"  • [{item[0]}] {item[1]} - {item[2]} (Envio: {item[3]}, {item[4]} dias úteis atrás)")
if len(due_for_followup) > 25:
    print(f"  ... e mais {len(due_for_followup) - 25} candidaturas.")

print("\n--- RECENTES / AINDA NÃO COBRÁVEIS (< 7 DIAS ÚTEIS) ---")
for item in upcoming_followup:
    print(f"  • [{item[0]}] {item[1]} - {item[2]} (Envio: {item[3]}, {item[4]} dias úteis atrás)")

conn.close()
