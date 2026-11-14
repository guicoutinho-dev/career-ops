# Security and Privacy Policy

## 1. Credentials and Secrets
- All credentials (OAuth tokens, API keys) must be loaded from OS environment variables.
- Secrets are never committed to Git, printed in logs, or sent in agent chat messages.
- The repository `.gitignore` strictly ignores `.env`, `*.token`, `credentials.json`, `data/*.db`, and `output/`.

## 2. Prompt Injection Defense
- Job descriptions, external web pages, and incoming emails are treated as **untrusted user input**.
- Sanitization strips directives that instruct agents to ignore rules, disclose secrets, elevate privileges, or auto-approve submissions.
- Strict isolation prevents prompt content from overriding application business logic.

## 3. Human Approval Gate
- No application submission or external message dispatch can occur without an explicit, cryptographically hashed manual approval token.
- Silence or timeout is never interpreted as approval.

## 4. Personal Data Protection
- Candidate contact information, address, phone number, and detailed CV data are restricted to local processing.
- Redacted diagnostic mode removes PII when logging or generating debugging reports.
