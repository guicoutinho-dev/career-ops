"""
Robust Google OAuth local flow.
Runs local server on port 8088 directly without state mismatch.
"""

import sys
import webbrowser
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow, _RedirectWSGIApp, _ExclusiveWSGIServer
import wsgiref.simple_server

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDENTIALS_FILE = Path("config/gmail_credentials.json")
TOKEN_FILE = Path("config/gmail_token.json")
URL_FILE = Path("config/gmail_auth_url.txt")

def main():
    if not CREDENTIALS_FILE.exists():
        print(f"[ERROR] Credential file not found at: {CREDENTIALS_FILE}", flush=True)
        sys.exit(1)

    print("==================================================", flush=True)
    print("      GOOGLE OAUTH 2.0 — AGUARDANDO LOGIN         ", flush=True)
    print("==================================================", flush=True)
    flow = InstalledAppFlow.from_client_secrets_file(
        str(CREDENTIALS_FILE),
        scopes=SCOPES
    )

    host = "localhost"
    port = 8088
    flow.redirect_uri = f"http://{host}:{port}/"
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")

    URL_FILE.write_text(auth_url, encoding="utf-8")
    print(f"\nAUTH_URL:\n{auth_url}\n", flush=True)

    wsgi_app = _RedirectWSGIApp("Autenticacao concluida com sucesso! Voce pode fechar esta aba.")
    local_server = wsgiref.simple_server.make_server(
        host,
        port,
        wsgi_app,
        server_class=_ExclusiveWSGIServer
    )

    try:
        webbrowser.open(auth_url)
        print("Servidor local ativo na porta 8088. Aguardando callback...", flush=True)
        local_server.handle_request()

        authorization_response = wsgi_app.last_request_uri.replace("http://", "https://")
        flow.fetch_token(authorization_response=authorization_response)

        creds = flow.credentials
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

        print(f"\n[SUCCESS] Token permanente salvo em: {TOKEN_FILE}", flush=True)
        if URL_FILE.exists():
            URL_FILE.unlink()
    except Exception as e:
        print(f"\n[ERROR] Falha: {e}", flush=True)
    finally:
        local_server.server_close()

if __name__ == "__main__":
    main()
