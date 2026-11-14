# Entrega 4 — SydneyStays Published Jobs Engine: Relatório do Piloto e Verificação

**Data:** 2026-11-14  
**Worktree:** `C:/Users/guicx/.codex/worktrees/sydneystays-employer-pilot/career-ops`  
**Branch:** `codex/sydneystays-published-jobs-design`  
**Base do branch:** `256c6c15`

## 1. Resumo Executivo

A infraestrutura técnica da Entrega 4 (**Backpacker Published Jobs Engine**) foi implementada com TDD, migração explícita e reversível de esquema SQLite (`v4 -> v5`), filtragem determinística de funções e localidades, leitura em streaming limitado por bytes na rede / taxa de compressão / elemento / registros para o feed gzip do Workable, ponte ATS validada por página oficial de carreiras, avaliador segregado da Adzuna AU, medidor de deslocamento TfNSW e compatibilidade preservada com o banco e a UI do Jobs Explorer.

No entanto, o **piloto ao vivo de primeira execução NÃO atingiu os critérios quantitativos de vagas publicadas verificadas**, devido a bloqueios externos legítimos e ausências de credenciais gratuitas confirmadas (respeitados integralmente, sem contorno):

1. **Workable XML (`https://www.workable.com/boards/workable.xml`):**
   - Diagnóstico pré-rate-limit em `--dry-run` e auditoria direta comprovaram que o feed transfere **139,94 MiB** compactados em `gzip` (`Transfer-Encoding: chunked`, sem `Content-Length`), expandindo em stream para **834,27 MiB** e **171.081** elementos `<job>`, dos quais **3.333** são `country="AU"` e **41** passaram no classificador pré-refinamento.
   - As URLs publicadas do feed (`https://apply.workable.com/j/<shortcode>`) respondem `HTTP 301` para `/<account>/j/<shortcode>` e servem um shell SPA cujo estado real vem de `https://apply.workable.com/api/v2/accounts/<account>/jobs/<shortcode>` (`state == "published"`). Isso foi corrigido via TDD em `run_published_jobs.py`, junto com a exclusão de falsos positivos observados (`Deckhand`, `Mechanic All Rounder`, `Master less than 45m`, `Medical Receptionist`, `Research Panel`, `Sales Consultant`).
   - Logo após as execuções diagnósticas, o CDN Cloudflare do Workable respondeu **`HTTP 429`** com **`Retry-After: 3584`** (~1 hora) para `GET workable.xml`. O runner falhou fechado de forma segura (`"error": "HTTPStatusError"`), preservando o banco sem tentar contornar o limite.
2. **Official ATS Seeds (`published_boards.yml`):** Nenhum empregador australiano testado expôs link estático não-redirecionado e não-bloqueado (`HTTP 200` sem bot-wall/JS-only) para boards Greenhouse/Lever/Ashby sem uso de datasets `CC BY-NC`. O manifesto foi mantido vazio (`config/published_boards.example.yml`: `boards: []`).
3. **Adzuna AU & TfNSW Trip Planner:** Variáveis `ADZUNA_FREE_QUOTA_CONFIRMED`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `TFNSW_FREE_QUOTA_CONFIRMED` e `TFNSW_API_KEY` não estão configuradas no ambiente; o runner registrou `skipped_free_quota_unconfirmed` e `transit.measured = 0` (`AU$0`).

## 2. Resultados da Execução ao Vivo e Idempotência

### Execução `--dry-run` inicial (pré-correção 301/SPA, antes do 429)
- Workable AU streamed: `fetched: 3333`, `eligible: 41`, `checked: 30`, `verified: 0`, `unverified: 30` (motivo: `http_301` em `/j/<shortcode>`).
- Adzuna: `skipped_free_quota_unconfirmed`.

### Execução `--live` Run 1 e Run 2 (ciclo inicial, pré-ajuste de `4xx` / `Retry-After`)
Comando: `python run_published_jobs.py --live --boards config/published_boards.example.yml --evaluate-adzuna --max-verifications 30`

- **Run 1:** `{"mode": "live", "sources": {"workable": {"fetched": 0, "eligible": 0, "verified": 0, "unverified": 0, "checked": 0, "error": "HTTPStatusError"}}, "transit": {"measured": 0, "unknown": 0}, "adzuna": {"status": "skipped_free_quota_unconfirmed"}}`
- **Run 2 (idempotência):** resultado idêntico (`0` mutações duplicadas, backup pré-v5 preservado e verificado).

### Execução `--live` pós-cooldown (`16:14:15Z` / `13:14:15-03:00`, após `b1783236`)
- Após aguardar o expirar da janela `Retry-After: 1784` (medida às `15:41:28 GMT`), foi executada **uma** nova chamada controlada:
  `python run_published_jobs.py --live --boards config/published_boards.example.yml --evaluate-adzuna --max-verifications 30`
- **Resultado observado:**
  `{"mode": "live", "sources": {"workable": {"fetched": 0, "eligible": 0, "verified": 0, "unverified": 0, "checked": 0, "error": "http_429_retry_after_3600"}}, "transit": {"measured": 0, "unknown": 0}, "adzuna": {"status": "skipped_free_quota_unconfirmed"}}`
- **Respeito ao novo cooldown:** conforme `b1783236` (`4xx`/`429` sem retry imediato), o runner preservou o header `Retry-After: 3600` (1 hora) e **não houve segunda requisição ao vivo** neste ciclo para não agravar o bloqueio da Cloudflare sobre `workable.xml`.

## 3. Integridade do SQLite e Preservação de Dados (`data/hub.db`)

Inspeção somente leitura (`file:data/hub.db?mode=ro`) após a execução pós-cooldown:
- `PRAGMA integrity_check`: `ok`
- `PRAGMA user_version`: `5`
- `opportunities`: `155` (`154` `OpenStreetMap discovery`, `1` `Manual official-site seed`, todos `employer_lead` preservados integralmente; `0` `verified_job`, `0` `unverified_job_post`)
- `opportunity_evidence`: `155`
- `published_source_keys`: `0`
- `commute_observations`: `0`

### Prova de Migração e Rollback (`v4 -> v5 -> v4`) em Cópia Temporária
Executado em cópia isolada de `data/hub.db` antes da migração ao vivo:
- `v4 -> v5` via `migrate_published_catalog`: `user_version = 5`, `PRAGMA integrity_check = ok`, `155` oportunidades.
- `v5 -> v4` via `restore_published_catalog_backup(..., force=True)`: `user_version = 4`, `PRAGMA integrity_check = ok`, `155` oportunidades e `155` evidências preservadas.

## 4. Critérios de Aceitação do Piloto (Medição Honesta)

| Critério | Meta do Plano/Spec | Medido (Live DB) | Diagnóstico / Observação | Status |
|---|---|---|---|---|
| Vagas publicadas recentes (<=30d & <=48h) `verified_job` | `>= 20` | `0` persistidas | Workable XML atingiu `HTTP 429` (`Retry-After: 3584`) após o diagnóstico; pré-429 havia ~35 anúncios `api:published` (incluindo ~11–13 recentes em NSW) | **Não atingido** |
| Vagas com tempo de deslocamento medido <=45 min | `>= 10` | `0` | `TFNSW_API_KEY` e `TFNSW_FREE_QUOTA_CONFIRMED` ausentes; precisão exata depende de coordenadas da vaga | **Não atingido** |
| Vagas com modalidade explícita na fonte | `>= 5` | `0` persistidas | — | **Não atingido** |
| Auditoria manual de `verified_job` (links válidos >=95%, 0 merges errados) | `100%` auditado, `>=95%` links, `0` merges | `N/A` (`0` novas persistidas); pré-429 `35/35` Workable `/api/v2/` confirmados abertos | — | **Bloqueado (429)** |
| Preservação de leads OSM e estado do usuário | `155` leads + `localStorage` | `155/155` preservados; Playwright fixture e DB real sem regressão | Sem perdas nem mutação de favoritos/notas | **Passou** |
| Gate de retenção de 7 dias | `7 dias` | `pending` | Requer operação contínua pós-liberação de fontes | **`pending`** |

**Veredito operacional do piloto:** **`pending` / bloqueado por coleta externa** (o código, testes e proteção do banco passaram; a cota de primeira execução não foi atingida porque `workable.xml` entrou em `HTTP 429` e Adzuna/TfNSW não têm credenciais/cota gratuita confirmada).

## 5. Suítes de Verificação Executadas (Pós-cooldown e ciclo de revisão final)

- **Python (`python -m pytest -ra` em `sydney-hub`):** `246 passed, 2 warnings in 9.70s` (inclui `tests/test_published_runner.py` com não-retry `4xx`/`429` e preservação de `Retry-After` de `b1783236`, além das correções da revisão final `e479f59d`).
- **Playwright UI (fixture `node tests/test_jobs_ui.mjs`):** passou com código de saída `0` (`Jobs UI: persisted API, evidence labels, saved state, mobile, and API failure passed.`).
- **Smoke API + Playwright contra cópia isolada de `data/hub.db` (`v5`):** `GET /api/opportunities?limit=500` -> `200`, `X-Total-Count: 155` (`155` itens); Chromium headless renderizou `155` cards (`LEADS: 155`, `VERIFIED: 0`, `PAGE_ERRORS: 0`).

## 6. Auditoria de Segurança

- O diff do branch `256c6c15..HEAD` não introduz credenciais, tokens ou arquivos `.env`.
- Arquivos já rastreados no upstream detectados via `git ls-files config/`:
  - `config/gmail_credentials.json`
  - `config/gmail_token.json`
  - `config/gmail_auth_url.txt`
  *(Nota: nenhum conteúdo desses arquivos foi exibido ou modificado; permanecem como dívida pré-existente do repositório upstream para revogação/rotação fora do escopo destrutivo deste branch.)*
- Nenhum dataset `CC BY-NC` (`Riley-Dorrington/job-board-aggregator`) foi utilizado em `published_boards.yml`.
- Limite Workable respeitou teto de `200 MiB` em wire bytes, razão máxima de descompressão `16x`, `2 MiB` por nó `<job>`, `10.000` registros AU retidos, `300.000` registros globais lidos em `O(1)` de DOM, sem desabilitar SSL nem redirecionar entre hosts.
