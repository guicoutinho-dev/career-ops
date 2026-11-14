# SydneyStays — Entrega 5: Jobs UX & Matching (Design Specification)

**Data:** 2026-11-14  
**Status:** Especificação aprovada (Alternativa A1 + semântica estrita de `null` + Inspector retrátil + preservação Olympian Atlas)  
**Commit-base (Entrega 4):** `d1d08591` (`codex/sydneystays-published-jobs-design`)  
**Worktree independente (Entrega 5):** `C:\Users\guicx\.codex\worktrees\sydneystays-jobs-v5\career-ops` (`branch: codex/sydneystays-jobs-v5-ux`)  
**Referência estética:** Impeccable (`SydneyStays · The Olympian Atlas`, modo Operate) — preservar `sydney-hub/web/atlas.css`, tema dark/light, Home, Stays, Tracker, Sources e barra global; **não executar `npx impeccable update`**.

> **Pendência operacional separada (fora do escopo da V5):** diversificar as fontes diretas de vagas publicadas (boards ATS oficiais adicionais, portais/feeds autorizados e/ou confirmação de licenciamento gratuito de Adzuna AU / TfNSW) para eliminar a dependência exclusiva do feed `workable.xml` (sujeito a `HTTP 429` da Cloudflare). **Não implementar dentro da Entrega 5.**

---

## 1. Diagnóstico do Estado Atual da Aba Jobs

1. **Mistura das três camadas (`template.html`):** `loadJobsCatalog()` carrega `/api/opportunities`, mas `#jobsListContainer` e `JobsMap` misturam `verified_job`, `unverified_job_post` e `employer_lead` na mesma lista e nos mesmos pinos, variando apenas texto de `.job-tag`.
2. **Sobrescrita destrutiva no cliente (`template.html:3975-3977`):** `loadJobsCatalog()` força `requirements: []`, `tickets_list: []` e `visa_88_days: false`, descartando metadados reais da API (`requirements`, `role_bucket`, `experience_required`, `ats_provider`, `suburb_match_basis`).
3. **Semântica `null` inconsistente:** filtro de salário (`pay_hourly_min || 0`) trata `null` como `0` e não oferece `pay_max` nem opção explícita `"Incluir sem informação"` (`Include unknown`).
4. **Conflito Drawer vs Inspector:** `#appInspector` (`#insBody`, `420px`) existe no shell para Stays (`body:not(.ws-stays) #appInspector { display: none }`), enquanto Jobs abre `#jobsDetailDrawer` (`z-index: 40`) sobrepondo o mapa no desktop.
5. **Lacunas pontuais de API:** `GET /api/opportunities` já oferece `verification_state`, `role_bucket`, `employment_type`, `requirement`, `origin_suburb`, `max_commute_minutes`, `commute_window`, `recent` e `commute_unknown`, mas ainda não filtra por faixa salarial (`pay_min` / `pay_max`), `include_unknown_*`, `category`, `ats_provider`, `recent_days`, nem expõe um endpoint agregado `/api/opportunities/summary` para contagens globais das 3 abas e facetas.

---

## 2. Decisão Aprovada: Opção A + Alternativa A1

### 2.1 Três segmentos na mesma aba Jobs
Controle segmentado (`role="tablist"`) no topo da aba Jobs:
- **Verified Jobs** (`verification_state = 'verified_job'`)
- **Community Posts** (`verification_state = 'unverified_job_post'`)
- **Employer Leads** (`verification_state = 'employer_lead'`)

Regras:
- Contagens dinâmicas consistentes com `/api/opportunities/summary` e filtros ativos.
- Nunca promover `employer_lead` ou `unverified_job_post` a vaga verificada.
- Quando `Verified Jobs` estiver vazio, exibir estado vazio orientativo com atalhos diretos para `Community Posts` e `Employer Leads`.

### 2.2 Composição Desktop e Mobile (Alternativa A1)
- **Desktop (`>= 1100px`):** **Lista + Mapa Interativo (`JobsMap`) + Inspector Contextual Retrátil (`#appInspector` / `#insBody`)** sem sobrepor o mapa. Retrátil via botão e atalho `I`, ajustando largura e disparando `JobsMap.resize()`. No modo `list`, mostra Lista + Inspector lado a lado.
- **Compacto / Mobile (`< 1100px` / `<= 850px`):** mantém alternância Lista/Mapa e abre `#jobsDetailDrawer` estilizado como **bottom sheet** / drawer usando exatamente o mesmo renderizador puro do Inspector desktop.

### 2.3 Filtros e Tratamento Obrigatório de `null`
- Filtros cobertos: Bairro/Região, Cargo/Categoria (`role_bucket`, `category`, `track`, busca textual), Salário (`payMin`, `payMax`), Modalidade (`employment_type`), Requisitos (`requirements`), Recência (`recentDays`), Fonte/Verificação (`source`, `ats_provider`) e Deslocamento TfNSW de Rockdale (`commuteWindow`, `maxCommuteMinutes`).
- **Sem filtro ativo:** registros com campo `null`/desconhecido permanecem visíveis.
- **Com filtro específico ativo:** correspondência estrita por padrão (exclui `null`), com toggle explícito `"Incluir sem informação"` por dimensão filtrável (`includeUnknownPay`, `includeUnknownType`, `includeUnknownReq`, `includeUnknownCommute`). Nunca converter `null` em `0`, `false` ou `"sem requisitos"`.

### 2.4 Cards, Mapa, Proveniência e Persistência
- **Cards compactos (máx. 3 linhas):** título real (para `employer_lead`: nome do estabelecimento + subtítulo de categoria/prospecção), bairro + precisão geográfica, salário/modalidade apenas se conhecidos, indicador de segmento (ícone + forma + rótulo, nunca só cor).
- **Mapa:** diferenciação visual por ícone/borda/formato por `verification_state`; registros sem coordenadas verificadas (`unknown`) nunca recebem pinos inventados; sincronia bidirecional card ↔ marcador ↔ cluster popover ↔ Inspector.
- **Inspector unificado:** evidência, `ats_provider`, `discovery_source`, `last_verified_at` (aviso `>14 dias`), janelas TfNSW de Rockdale (ou `"Trajeto não verificado"`), atribuição ODbL OpenStreetMap e ações de candidatura/contato/notas.
- **Persistência integral:** `sydney_jobs_favorites_v1`, `sydney_jobs_status_v1`, `sydney_jobs_notes_v1`, `sydney_jobs_collections_v1`, `sydney_jobs_saved_searches_v1`, `sydney_jobs_view_mode_v1`, `sydney_jobs_split_pct_v1` e as 3 trilhas (`Sydney Immediate`, `88 Days Regional`, `SAP / Tech`).
