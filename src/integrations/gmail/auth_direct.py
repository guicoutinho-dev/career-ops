"""
Direct copy-paste token exchange.
No local server required.
Usage:
    python -m src.integrations.gmail.auth_direct
"""

import sys
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDENTIALS_FILE = Path("config/gmail_credentials.json")
TOKEN_FILE = Path("config/gmail_token.json")

def main():
    if not CREDENTIALS_FILE.exists():
        print(f"[ERROR] Credential file not found at: {CREDENTIALS_FILE}")
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CREDENTIALS_FILE),
        scopes=SCOPES,
        redirect_uri="http://localhost:8088/"
    )

    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")

    print("\n" + "="*70)
    print("                GOOGLE OAUTH LOGIN DIRECTO           ")
    print("="*70)
    print("1. Abra o link abaixo no seu navegador:\n")
    print(auth_url)
    print("\n" + "="*70)
    print("2. Após autorizar, o navegador tentará redirecionar para http://localhost:8088/?code=...")
    print("Se a página der 'Não é possível acessar esse site', copie a URL inteira da barra de endereço e cole aqui.")
    print("="*70 + "\n")

    redirect_response = input("Cole a URL completa de redirecionamento (ou o código): ").strip()
    if not redirect_response:
        print("[ABORT] Nenhum código informado.")
        return

    if "code=" in redirect_response:
        # User pasted full URL
        flow.fetch_token(authorization_response=redirect_response)
    else:
        # User pasted raw code
        flow.fetch_token(code=redirect_response)

    creds = flow.credentials
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(creds.to_json())

    print(f"\n[SUCCESS] Token salvo com sucesso em: {TOKEN_FILE}")

if __name__ == "__main__":
    main()
