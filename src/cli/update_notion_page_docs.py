"""Update embedded blocks inside the 6 tailored job pages in Notion."""
from pathlib import Path
from src.services.env import load_env
load_env()
from src.integrations.notion.adapter import NotionAdapter

a = NotionAdapter()
st, res = a._http_request("POST", f"databases/{a.database_id}/query", {"page_size": 100})
rows = res.get("results", [])

apps = {
    "app_002": "Queensland",
    "app_003": "Cloud pro",
    "app_004": "Slade Group",
    "app_005": "Experis",
    "app_006": "Weston",
    "app_seek_95482398": "Recruitment Hive"
}

for app_id, pattern in apps.items():
    page = next((r for r in rows if pattern.lower() in "".join(t["plain_text"] for t in r["properties"]["Company"]["rich_text"]).lower()), None)
    if not page:
        continue
    pkg_dir = Path("applications") / app_id
    cv_files = list(pkg_dir.glob("cv-*.pdf"))
    cl_files = list(pkg_dir.glob("cover-letter-*.pdf"))
    if not cv_files or not cl_files:
        continue

    page_id = page["id"]
    cv_uid = a.upload_file(cv_files[0])
    cl_uid = a.upload_file(cl_files[0])
    if not cv_uid or not cl_uid:
        print(app_id, "--> skipped: both replacement PDFs must upload successfully")
        continue

    # Append the verified replacement documents. Existing page blocks are
    # preserved; deletion is not part of this sync script.
    comp_name = "".join(t["plain_text"] for t in page["properties"]["Company"]["rich_text"])
    loc_name = "".join(t["plain_text"] for t in page["properties"]["Location"]["rich_text"])

    blocks = [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": f"📄 Documentos Localizados ({loc_name}) — Download Direto"}}]
            }
        },
        {
            "object": "block",
            "type": "file",
            "file": {
                "file_upload": {"id": cv_uid},
                "caption": [{"type": "text", "text": {"content": f"Tailored CV ({loc_name} Header + WHV 462) — {cv_files[0].name}"}}]
            }
        },
        {
            "object": "block",
            "type": "file",
            "file": {
                "file_upload": {"id": cl_uid},
                "caption": [{"type": "text", "text": {"content": f"Tailored Cover Letter ({comp_name}) — {cl_files[0].name}"}}]
            }
        },
        {
            "object": "block",
            "type": "callout",
            "callout": {
                "icon": {"type": "emoji", "emoji": "📋"},
                "rich_text": [{
                    "type": "text",
                    "text": {"content": f"Revise as respostas e declarações específicas desta candidatura no pacote local: applications/{app_id}/answers.json. Não inferir autorização de trabalho, visto ou disponibilidade a partir deste registro."}
                }]
            }
        }
    ]
    st_b, _ = a._http_request("PATCH", f"blocks/{page_id}/children", {"children": blocks})
    print(app_id, f"({comp_name})", "--> page blocks updated:", st_b)

print("[OK] All page blocks updated with localized PDFs.")
