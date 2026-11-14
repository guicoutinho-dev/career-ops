# SydneyStays Jobs V5 — UX & Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (Native flow) or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evoluir exclusivamente a aba **Jobs** do SydneyStays para separar visual e funcionalmente `Verified Jobs`, `Community Posts` e `Employer Leads`, aplicar filtros fiéis à semântica `null` (correspondência estrita + `"Include unknown"`), e consolidar a experiência desktop **Lista + Mapa + Inspector retrátil** (Alternativa A1) com mobile em bottom sheet, preservando `SydneyStays · The Olympian Atlas`, SQLite v4/v5 e todas as interações salvas do usuário.

**Architecture:** Extensões aditivas e retrocompatíveis em `CatalogRepository` e `GET /api/opportunities` (+ novo `GET /api/opportunities/summary`), combinadas a um módulo puro testável em Node (`sydney-hub/web/jobs-v5-engine.js`) para normalização sem sobrescrita destrutiva, cálculo de contagens, filtros com `Include unknown` e construção de modelos de detalhe compartilhados entre `#appInspector` (desktop) e `#jobsDetailDrawer` (mobile/compacto) em `sydney-hub/web/template.html` e `sydney-hub/web/atlas.css`.

**Tech Stack:** Python 3.12, FastAPI, SQLite (schema v4/v5 inalterado), Vanilla JS (`jobs-v5-engine.js` UMD + `template.html`), CSS (`atlas.css`), MapLibre GL JS, `pytest`, Node test runner (`node --test`), Playwright (`tests/test_jobs_ui.mjs`), Impeccable CLI (`critique` / `polish` / `audit` após implementação, sem atualizar versão).

**Spec:** `docs/superpowers/specs/2026-11-14-sydneystays-jobs-v5-ux-design.md`

**Worktree & Isolation Strategy:**
- **Commit-base:** `d1d08591` (`codex/sydneystays-published-jobs-design`, estado validado da Entrega 4).
- **Worktree da V5:** `C:\Users\guicx\.codex\worktrees\sydneystays-jobs-v5\career-ops` (`branch: codex/sydneystays-jobs-v5-ux`).
- **Isolamento obrigatório:** Nunca modificar `C:\Users\guicx\.codex\worktrees\sydneystays-employer-pilot\career-ops`, seu `sydney-hub/data/hub.db`, nem executar comandos contra o ambiente publicado, nem executar `merge`, `push` ou `deploy`. Todos os testes usam `tmp_path`, fixtures ou cópias temporárias isoladas.

**Operational Backlog (OUT OF SCOPE for V5 — DO NOT IMPLEMENT HERE):**
- Diversificar fontes diretas de vagas publicadas (novos boards ATS oficiais, portais públicos autorizados e/ou confirmação comercial gratuita da Adzuna AU / TfNSW) para reduzir dependência exclusiva de `workable.xml` (sujeito a Cloudflare `HTTP 429`).

---

## Global Constraints

- **Escopo estrito de UI:** Alterar **exclusivamente** a aba Jobs (e o CSS compartilhado mínimo para reabilitar `#appInspector` quando `body.ws-jobs` estiver ativo no desktop). **Não alterar** Home/Stays (`renderStays*`, `#staysView`), Tracker (`#trackerView`), Sources (`#sourcesView`) ou barra de navegação global (`.app-toolbar`).
- **Preservação estética (`Olympian Atlas`):** Manter tema dark/light, `#0b1230`, `#101b56`, `#173cc9`, `#a9bdff`, `#f5f0e7`, `#171d34`, `zeus-engraving.webp`, Cormorant Garamond, Barlow Condensed e Inter com numerais tabulares. Não copiar Hermes Agent.
- **Zero migração destrutiva:** Não alterar schema SQLite (`user_version` permanece `4`/`5`), preservar `.employer-pilot-rollback.json` e `.published-v5-rollback.json`, e preservar todos os contratos V4 de `/api/opportunities` (`recent`, `commute_window`, `commute_unknown`, `X-Total-Count`).
- **Honestidade de dados e `null`:** Nunca converter `null` em `0`, `false` ou `"sem requisitos"`; nunca promover `employer_lead` ou `unverified_job_post` a `verified_job`; nunca fabricar coordenadas no mapa quando `lat`/`lng`/subúrbio forem desconhecidos; nunca inventar tempos de trajeto quando `commute_windows` não existir.
- **Persistência intacta:** Manter exatamente as chaves `sydney_jobs_favorites_v1`, `sydney_jobs_status_v1`, `sydney_jobs_notes_v1`, `sydney_jobs_collections_v1`, `sydney_jobs_saved_searches_v1`, `sydney_jobs_view_mode_v1`, `sydney_jobs_split_pct_v1` indexadas por `opportunity.id` estável, e preservar as 3 trilhas (`Sydney Immediate`, `88 Days Regional`, `SAP / Tech`).
- **Proibições operacionais:** Sem credenciais/OAuth Gmail tocados, sem scraping novo de Facebook, sem dependências pagas, sem `npx impeccable update`, sem `merge`, `push` ou `deploy`.

---

## Review Focus

1. **`null` coercion trap em salário, modalidade e requisitos:** Sem filtro ativo, registros com `pay_hourly_min=None`, `employment_type=None` ou `requirements=None` devem aparecer; ao filtrar por `$25+/h` ou `Casual` ou `RSA`, registros `null` devem sair por padrão e retornar somente quando `"Incluir sem informação"` estiver ligado (Tasks 1, 2 e 3).
2. **Sobrescrita silenciosa de `requirements`/`tickets_list` em `loadJobsCatalog()`:** `requirements` não-nulo vindo da API (texto ou JSON array) precisa chegar estruturado ao filtro/card/Inspector, enquanto `null` continua `null` sem virar array vazio tratado como `"sem requisitos"` (Tasks 2 e 3).
3. **Seleção órfã entre segmentos, filtros, paginação e redimensionamento de viewport:** Trocar de segmento (`Verified Jobs` ↔ `Employer Leads`), alterar filtros ou redimensionar entre `1440px` e `390px` não pode deixar o `#appInspector` exibindo registro de outro segmento, nem abrir `#jobsDetailDrawer` por cima do mapa no desktop, nem apagar favoritos/notas/status (Tasks 5 e 6).
4. **Coordenadas desconhecidas e clusters mistos no mapa:** Registros com `locationPrecision === 'unknown'` nunca podem ganhar pino aproximado; marcadores de cluster misto precisam informar composição sem depender apenas de cor (Task 4).
5. **Compatibilidade v4 + v5 + `test_web_contract.py`:** `build_dashboard(tmp_path)` em `tests/test_web_contract.py` e queries em banco SQLite `user_version == 4` (sem tabelas v5 `commute_observations`/`published_source_keys`) não podem quebrar com as novas rotas/parâmetros de API (Task 1 e Task 3).

---

## File Structure

| Arquivo | Responsabilidade |
|---|---|
| `sydney-hub/hub/storage/catalog.py` | Filtros aditivos em `CatalogRepository._build_filters` / `count` / `list` (`pay_min`, `pay_max`, `include_unknown_*`, `category`, `ats_provider`, `recent_days`) e `summary()` |
| `sydney-hub/hub/api/app.py` | Parâmetros opcionais em `GET /api/opportunities` e novo endpoint `GET /api/opportunities/summary` |
| `sydney-hub/web/jobs-v5-engine.js` *(novo)* | Módulo UMD puro: normalização segura sem apagar `requirements`/`tickets_list`, contagem por segmento, filtragem com `Include unknown`, modelo puro de card/marcador/Inspector |
| `sydney-hub/web/template.html` | Segmentos Jobs, filtros refinados com toggles `Include unknown`, cards compactos, pinos diferenciados, `#appInspector` ancorado no desktop + `#jobsDetailDrawer` bottom sheet no mobile, estados de erro/loading/vazio |
| `sydney-hub/web/atlas.css` | Tokens semânticos por segmento, layout desktop 3 zonas retrátil (`#jobsView` + `#appInspector`), bottom sheet mobile e foco/acessibilidade WCAG AA+, preservando Olympian Atlas |
| `sydney-hub/web/index.html` | Recompilado deterministicamente via `python web/build_dashboard.py` após alterações em `template.html` |
| `sydney-hub/tests/test_api.py`, `test_catalog.py` | Testes TDD dos novos filtros, `Include unknown`, `/api/opportunities/summary`, `422`, e compatibilidade v4/v5 |
| `sydney-hub/tests/test_jobs_v5_engine.mjs` *(novo)* | Testes unitários Node (`node --test`) para engine pura de matching e `null` semantics |
| `sydney-hub/tests/test_jobs_ui.mjs`, `test_web_contract.py` | Regressão E2E Playwright desktop (`1440x900`, `1200x800`) + mobile (`390x844`), teclado, persistência, API error/vazio e contrato do template |

---

### Task 1: Backend Filtering (`pay_min`/`pay_max`, `include_unknown_*`, `recent_days`) & `/api/opportunities/summary`

**Files:**
- Modify: `sydney-hub/hub/storage/catalog.py:385-543`
- Modify: `sydney-hub/hub/api/app.py:177-211`
- Test: `sydney-hub/tests/test_api.py`, `sydney-hub/tests/test_catalog.py`

**Interfaces:**
- Produces in `CatalogRepository`:
  - `count(..., pay_min: float | None = None, pay_max: float | None = None, include_unknown_pay: bool = False, include_unknown_employment_type: bool = False, include_unknown_requirement: bool = False, category: str | None = None, ats_provider: str | None = None, recent_days: int | None = None) -> int`
  - `list(..., [same optional filters], limit: int = 500, offset: int = 0) -> list[dict]`
  - `summary(*, role: str | None = None, suburb: str | None = None, location: str | None = None, source: str | None = None, employment_type: str | None = None, experience: str | None = None, requirement: str | None = None, origin_suburb: str | None = None, max_commute_minutes: int | None = None, commute_window: str | None = None, role_bucket: str | None = None, recent: bool | None = None, commute_unknown: bool | None = None, pay_min: float | None = None, pay_max: float | None = None, include_unknown_pay: bool = False, include_unknown_employment_type: bool = False, include_unknown_requirement: bool = False, category: str | None = None, ats_provider: str | None = None, recent_days: int | None = None) -> dict` returning `{"total": int, "counts_by_state": {"verified_job": int, "unverified_job_post": int, "employer_lead": int}, "facets": {"suburbs": list[str], "categories": list[str], "role_buckets": list[str], "employment_types": list[str], "sources": list[str], "ats_providers": list[str]}}`.
- Produces HTTP endpoint `GET /api/opportunities/summary` accepting the same query filters (except `verification_state`, `limit`, `offset`) and returning HTTP `200` JSON or `422` when `pay_min > pay_max` or invalid commute args.
- Consumes existing SQLite v4/v5 tables (`opportunities`, `employers`, `opportunity_evidence`, `commute_observations`).

- [ ] **Step 1: Write failing tests (API + Catalog)**

Add tests to `sydney-hub/tests/test_api.py` and `sydney-hub/tests/test_catalog.py` asserting:
1. `test_opportunities_pay_filters_and_include_unknown_semantics`:
   - Item A (`pay_hourly_min=28.0`), Item B (`pay_hourly_min=42.0`), Item C (`pay_hourly_min=None`).
   - No pay filter: returns `[A, B, C]`.
   - `pay_min=30`: returns `[B]` (strict; `None` NOT converted to `0`).
   - `pay_min=30&include_unknown_pay=true`: returns `[B, C]`.
   - `pay_min=25&pay_max=35`: returns `[A]`.
   - `pay_min=40&pay_max=25`: returns HTTP `422`.
2. `test_opportunities_employment_type_and_requirement_include_unknown`:
   - Matching `employment_type=Casual` excludes `NULL`/`""` by default, includes them when `include_unknown_employment_type=true`.
   - Matching `requirement=RSA` excludes `NULL`/`""` by default, includes them when `include_unknown_requirement=true`.
3. `test_opportunities_category_ats_provider_recent_days_filters`:
   - Filters `category`, `ats_provider`, and `recent_days=7` strictly on `published_at` / `last_verified_at` without breaking V4 `recent=true|false`.
4. `test_opportunities_summary_counts_and_facets_on_v4_and_v5`:
   - Returns accurate `counts_by_state` across >0 rows in all 3 states plus sorted distinct `facets` even when `limit=1` pagination is used on `/api/opportunities`, and passes on both v4 and v5 schemas.

- [ ] **Step 2: Run RED**

Run: `python -m pytest -q tests/test_api.py tests/test_catalog.py` (from `sydney-hub/`)  
Expected: FAIL (`TypeError` / unexpected keyword or `404` on `/api/opportunities/summary`, and unapplied `pay_min` returning all rows).

- [ ] **Step 3: Implement backend filters & summary in `catalog.py` and `app.py`**

- In `CatalogRepository._build_filters`:
  - Add exact equality filters for `o.category` and `o.ats_provider`.
  - For `o.employment_type`: when `employment_type` is provided, filter `(o.employment_type = ? OR o.employment_type IS NULL OR TRIM(o.employment_type) = '')` if `include_unknown_employment_type`, else `o.employment_type = ?`.
  - For `o.requirements`: when `requirement` is provided, filter `(o.requirements LIKE ? ESCAPE '\\' OR o.requirements IS NULL OR TRIM(o.requirements) = '')` if `include_unknown_requirement`, else `o.requirements LIKE ? ESCAPE '\\'`.
  - For `pay_min` / `pay_max`: validate `pay_min <= pay_max` (`ValueError`), build range condition `(o.pay_hourly_min >= ?)` / `(o.pay_hourly_min <= ?)`, wrapped in `(o.pay_hourly_min IS NULL OR (...))` when `include_unknown_pay=True`. Never coerce `NULL` to `0`.
  - For `recent_days`: require `julianday(COALESCE(o.published_at, o.last_verified_at)) >= julianday(?)` and `<= julianday(now)` without mutating V4's strict `recent` SQL clause.
- Add `CatalogRepository.summary(...)` running one parameterized aggregate `GROUP BY o.verification_state` query + distinct non-null facet queries (`suburb`, `category`, `role_bucket`, `employment_type`, `source`, `ats_provider`). Apply the `verified_job` evidence check consistently so `counts_by_state['verified_job']` matches `list(verification_state='verified_job')`.
- Wire parameters & `GET /api/opportunities/summary` in `sydney-hub/hub/api/app.py` (raising `HTTPException(422, ...)` when `pay_min is not None and pay_max is not None and pay_min > pay_max`).

- [ ] **Step 4: Run GREEN**

Run: `python -m pytest -ra tests/test_api.py tests/test_catalog.py tests/test_published_catalog.py tests/test_storage.py`  
Expected: ALL PASS with 0 failures.

- [ ] **Step 5: Commit**

```bash
git add sydney-hub/hub/storage/catalog.py sydney-hub/hub/api/app.py sydney-hub/tests/test_api.py sydney-hub/tests/test_catalog.py
git commit -m "feat(api): add V5 matching filters, null semantics, and summary endpoint"
```

---

### Task 2: Pure Frontend Engine (`jobs-v5-engine.js`) for Normalization, `null`-Safe Filters & Shared Detail Model

**Files:**
- Create: `sydney-hub/web/jobs-v5-engine.js`
- Create: `sydney-hub/tests/test_jobs_v5_engine.mjs`

**Interfaces:**
- Exported on `globalThis.SydneyJobsV5` (and ES module `export` / CommonJS safe via conditional export or dual wrapper for `node --test`):
  - `normalizeOpportunity(rawJob: object, idx: number, classifyGeoFn: function) -> object` (parses `requirements` safely when string/JSON array, keeps `null` when unknown, extracts known structured `tickets_list` without inventing `"No ticket required"` when null, normalizes `role_bucket`, `ats_provider`, `pay_hourly_min`, `salaryMinParsed`/`salaryMaxParsed`, `commute_windows`, `verification_state`)
  - `computeSegmentCounts(allItems: Array, filterState: object, userStatus: object, userFavorites: object) -> { verified_job: number, unverified_job_post: number, employer_lead: number, total: number }`
  - `filterOpportunities(allItems: Array, filterState: object, deps: { userStatus, userFavorites, ignoreBounds, ignoreSegment }) -> Array`
  - `sortOpportunities(items: Array, sortBy: string) -> Array` (handles `pay_desc` placing `null` salaries last, never treating `null` as `$0` ahead of real numbers or vice-versa)
  - `buildJobDetailModel(job: object, context: { items, userStatus, userFavorites, userNotes }) -> object` (single normalized presentation model for Card, Map Marker/Popover, Desktop Inspector, and Mobile Drawer — including honest `"Não informado"` / `"Trajeto não verificado"` labels, OSM ODbL attribution rule, stale `>14d` badge rule, and CTA semantics per `verification_state`).

- [ ] **Step 1: Write failing unit tests (`sydney-hub/tests/test_jobs_v5_engine.mjs`)**

Using Node's built-in `node:test` and `node:assert/strict`:
1. `normalizeOpportunity` preserves real `requirements` (`'["RSA","White Card"]'` and `'RSA; Driver Licence'`), preserves `requirements: null` as `null` (not `[]`), never invents `visa_88_days` or `tickets_list` when absent.
2. `filterOpportunities` enforces strict `null` exclusion on `payMin`, `payMax`, `type`, `req`, `recentDays`, `maxCommuteMinutes` by default AND includes `null` records only when respective `includeUnknown*` flag is `true`.
3. `filterOpportunities` separates `verified_job`, `unverified_job_post`, `employer_lead` cleanly while allowing `computeSegmentCounts` to count each segment using the non-segment filters.
4. `sortOpportunities(..., 'pay_desc')` always places known numeric salaries before `null` salaries.
5. `buildJobDetailModel` sets distinct non-color glyphs/labels for each `verification_state`, prevents `employer_lead` from showing `"Candidatar-se"` as an open vacancy, preserves `commute_windows` vs `"Trajeto não verificado"`, and keeps ODbL attribution whenever `discovery_source === 'OpenStreetMap discovery'` even if `ats_provider` is present.

- [ ] **Step 2: Run RED**

Run: `node --test sydney-hub/tests/test_jobs_v5_engine.mjs`  
Expected: FAIL (`ERR_MODULE_NOT_FOUND` for `jobs-v5-engine.js`).

- [ ] **Step 3: Implement `sydney-hub/web/jobs-v5-engine.js`**

Write clean, modular, dependency-free pure JS functions in `sydney-hub/web/jobs-v5-engine.js` exposing `export { ... }` for ESM tests AND attaching to `window.SydneyJobsV5` when loaded via `<script>` (using lightweight ESM-compatible or clean dual-entry wrapper — wait: `<script type="module">` vs classic `<script>`: in `template.html` inline `<script>` functions are called by `onclick="..."` HTML attributes, so classic `<script src="/jobs-v5-engine.js?v=5"></script>` attaching `globalThis.SydneyJobsV5` AND a small `if (typeof module !== 'undefined') module.exports = api;` allows `tests/test_jobs_v5_engine.mjs` to `import { createRequire } from 'node:module'` cleanly without breaking inline globals in `template.html`!).

- [ ] **Step 4: Run GREEN**

Run: `node --test sydney-hub/tests/test_jobs_v5_engine.mjs`  
Expected: ALL unit tests PASS.

- [ ] **Step 5: Commit**

```bash
git add sydney-hub/web/jobs-v5-engine.js sydney-hub/tests/test_jobs_v5_engine.mjs
git commit -m "feat(jobs-v5): add pure matching engine with strict null semantics"
```

---

### Task 3: Three-Segment Tabs, Honest Empty States & Filter Controls (`template.html`, `atlas.css`)

**Files:**
- Modify: `sydney-hub/web/template.html` (`loadJobsCatalog`, `#jobsHeaderPane` segment bar, `#jobsFiltersDialog`, `renderJobFacets`, `renderActiveJobFilters`, `updateJobsFilterBadge`, `resetAllJobFilters`, `renderJobs`, `renderJobsList`, saved searches migration)
- Modify: `sydney-hub/web/atlas.css` (segment tabs styling, `Include unknown` checkboxes/pills, Olympian Atlas semantic tokens)
- Rebuild: `sydney-hub/web/index.html` (`python web/build_dashboard.py`)
- Test: `sydney-hub/tests/test_jobs_ui.mjs`, `sydney-hub/tests/test_web_contract.py`

**Interfaces:**
- Consumes `SydneyJobsV5` from `jobs-v5-engine.js` and `GET /api/opportunities` + `GET /api/opportunities/summary`.
- Produces DOM segment tabs `#jobsSegmentTabs` (`button[data-segment="verified_job"]`, `unverified_job_post`, `employer_lead`) with `role="tablist"` / `aria-selected`, segment counts synced from `computeSegmentCounts` + summary, `state.jobs.segment` (default `'verified_job'`), and filter state fields: `payMin`, `payMax`, `roleBucket`, `source`, `atsProvider`, `recentDays`, `commuteWindow`, `maxCommuteMinutes`, `includeUnknownPay`, `includeUnknownType`, `includeUnknownReq`, `includeUnknownCommute`.

- [ ] **Step 1: Write failing Playwright & contract tests**

Extend `sydney-hub/tests/test_jobs_ui.mjs` (and `tests/test_web_contract.py` to check `/jobs-v5-engine.js` script presence and absence of hardcoded `requirements: []` overwrite) to assert:
1. Segment tabs `Vagas verificadas`, `Anúncios comunitários`, `Empresas para prospecção` render with accurate counts matching API data; switching tabs filters strictly by `verification_state`.
2. When `verified_job` count is `0`, the empty state explains honestly that zero verified vacancies are present and offers working buttons to switch to `Community Posts` or `Employer Leads`.
3. `requirements` from API (`"RSA; White Card"`) populate `#jobFacetFilters` / card / detail panel (no longer overwritten to `[]`), while an item with `requirements: null` stays visible with no active requirement filter, disappears when filtering `RSA`, and reappears when toggling `"Incluir sem requisitos informados"`.
4. Same strict + `"Incluir sem salário informado"` / `"Incluir modalidade não informada"` / `"Incluir trajeto não verificado"` behavior verified for `payMin`/`payMax`, `employment_type`, and Rockdale TfNSW `commute_window`/`max_commute_minutes`.
5. `localStorage` keys (`sydney_jobs_favorites_v1`, `sydney_jobs_status_v1`, `sydney_jobs_notes_v1`, `sydney_jobs_collections_v1`, `sydney_jobs_saved_searches_v1`) and 3 tracks (`Sydney Immediate`, `88 Days Regional`, `SAP / Tech`) remain untouched across segment switches and page reload.

- [ ] **Step 2: Run RED**

Run: `python -m pytest -q tests/test_web_contract.py` and `node tests/test_jobs_ui.mjs`  
Expected: FAIL (missing `#jobsSegmentTabs`, missing `Include unknown` controls, old `requirements: []` overwrite).

- [ ] **Step 3: Implement segment tabs, `Include unknown` filter controls & `loadJobsCatalog()` fix**

- Load `/jobs-v5-engine.js?v=5` in `template.html` `<head>`.
- Update `loadJobsCatalog()` to fetch `/api/opportunities` (paginated via `X-Total-Count`) and optional non-blocking `/api/opportunities/summary` into `jobsSummaryMeta`, mapping records via `SydneyJobsV5.normalizeOpportunity(...)` without discarding `requirements` or forcing `visa_88_days: false`.
- Add accessible `#jobsSegmentTabs` (`role="tablist"`) inside `#jobsHeaderPane` / subbar with distinct SVG icons + count badges + subtle surface tokens in `atlas.css`.
- Upgrade `#jobsFiltersDialog` and `renderJobFacets()` to expose:
  - Bairro (`suburb`), Cargo/Bucket (`role_bucket`), Trilha (`track`), Categoria (`category`), Salário (`payMin`/`payMax` inputs + presets + `"Incluir sem salário"`), Modalidade (`employment_type` + `"Incluir sem modalidade"`), Requisitos (`req` + `"Incluir sem requisitos"`), Recência (`recentDays`: 1/3/7/30 + `"Incluir sem data"`), Fonte/ATS (`source` / `ats_provider`), Deslocamento Rockdale (`commuteWindow`: `tuesday_0900`/`friday_1800`, `maxCommuteMinutes`: 30/45/60 + `"Incluir trajeto não verificado"`).
- Note contextual cue when `state.jobs.segment === 'employer_lead'`: clearly indicate vacancy-only filters (`Salário`, `Modalidade`, `Requisitos`) may hide leads unless `"Incluir sem informação"` is enabled.
- Run `python web/build_dashboard.py`.

- [ ] **Step 4: Run GREEN**

Run: `python -m pytest -q tests/test_web_contract.py` and `node tests/test_jobs_ui.mjs`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sydney-hub/web/template.html sydney-hub/web/atlas.css sydney-hub/web/index.html sydney-hub/tests/test_jobs_ui.mjs sydney-hub/tests/test_web_contract.py
git commit -m "feat(jobs-v5): add 3-segment navigation and null-safe matching filters"
```

---

### Task 4: Compact Cards, Non-Color-Only Map Pins & Bidirectional Sync (`template.html`, `atlas.css`)

**Files:**
- Modify: `sydney-hub/web/template.html` (`renderJobsList`, `JobsMap.buildClusters`, `JobsMap.render`, `showMapGroupPopover`, `classifyJobGeo`)
- Modify: `sydney-hub/web/atlas.css` (compact 3-line card rhythm, `.map-pill-marker` verification variants with non-color cues & legend)
- Rebuild: `sydney-hub/web/index.html`
- Test: `sydney-hub/tests/test_jobs_ui.mjs`

**Interfaces:**
- Consumes `SydneyJobsV5.buildJobDetailModel`.
- Produces clean card badges (max 3 tags), marker classes `.map-pill-marker[data-verification="verified_job|unverified_job_post|employer_lead|mixed"]` with SVG/shape glyphs + updated `#jobsMapLegend`, and cluster popover entries showing per-item segment icon + label.

- [ ] **Step 1: Write failing Playwright assertions**

Add assertions to `sydney-hub/tests/test_jobs_ui.mjs` verifying:
1. Cards show concise 3-line hierarchy without badge overflow (`<= 3` secondary tags in `.job-card-tags`); `employer_lead` cards clearly identify `"Empresa · prospecção"` and never render a fake job salary/title.
2. Map markers expose `data-verification` / distinct non-color glyph + accessible `aria-label` differentiating `verified_job`, `unverified_job_post`, `employer_lead`, and mixed clusters; items with `locationPrecision === 'unknown'` render `0` markers on the map (`#legendUnlocatedCount` accurate).
3. Clicking a card highlights the corresponding marker; clicking a map marker (or cluster popover item) selects the corresponding card (`class="selected"`, `aria-pressed="true"`).

- [ ] **Step 2: Run RED**

Run: `node tests/test_jobs_ui.mjs`  
Expected: FAIL on missing non-color marker differentiation / compact card selectors.

- [ ] **Step 3: Implement compact cards & differentiated map markers**

- Refine `renderJobsList()` card template in `template.html` and CSS in `atlas.css` to 3 clean lines:
  1. Title (`company` primary for `employer_lead`, `title` for job listings) + bookmark button
  2. Secondary line (`company · category` or `category · prospecção`) + Location line with geo precision dot
  3. Bottom row: Verification pill (icon + text) + Salary (only if known, `tabular-nums`) + Modality (only if known) + up to 1 extra essential tag (`Recente` or `Trajeto`). Move secondary evidence/requirements/commute windows to the Inspector.
- Update `JobsMap.buildClusters()` & `JobsMap.render()` to compute cluster verification composition (`verified_job` | `unverified_job_post` | `employer_lead` | `mixed`), render distinct non-color marker borders/icons (`✓` shield, dashed post bubble, square storefront icon, multi-dot badge), update `#jobsMapLegend`, and render clear segment icons in `showMapGroupPopover()`.
- Keep `classifyJobGeo()` strictly refusing coordinates when `suburb`/`lat`/`lng` are unknown.
- Rebuild `index.html`.

- [ ] **Step 4: Run GREEN**

Run: `node tests/test_jobs_ui.mjs`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sydney-hub/web/template.html sydney-hub/web/atlas.css sydney-hub/web/index.html sydney-hub/tests/test_jobs_ui.mjs
git commit -m "feat(jobs-v5): refine compact cards and non-color map differentiation"
```

---

### Task 5: Unified Desktop Collapsible `#appInspector` (`renderJobsInspector`) + Mobile Bottom Sheet (`#jobsDetailDrawer`)

**Files:**
- Modify: `sydney-hub/web/template.html` (`renderJobsInspector`, `buildJobDetailHtml`, `buildJobDetailActionsHtml`, `selectJob`, `openJobDetail`, `closeJobDetail`, `toggleInspector`, `switchWorkspace`, responsive listener, `renderJobs`)
- Modify: `sydney-hub/web/atlas.css` (`body.ws-jobs` 3-zone `#appInspector` layout, collapsed toggle chip/button, `1100px`–`1280px` width balance, `< 1100px` bottom-sheet styles for `#jobsDetailDrawer`)
- Rebuild: `sydney-hub/web/index.html`
- Test: `sydney-hub/tests/test_jobs_ui.mjs`, `sydney-hub/tests/test_web_contract.py`

**Interfaces:**
- Produces `renderJobsInspector(job, listItems)` rendering directly into `#appInspector` (`#insTitle` / `#insBody`) on desktop (`>= 1100px`) with zero `#jobsDetailDrawer` overlap.
- Produces `buildJobDetailHtml(job, listItems)` shared 1:1 between `renderJobsInspector()` (desktop) and `renderJobDetailDrawer()` (mobile/compact `< 1100px`).
- Preserves `renderStaysInspector()` and `body.ws-stays` `#appInspector` behavior unchanged.

- [ ] **Step 1: Write failing Playwright & contract tests**

Update & expand `sydney-hub/tests/test_jobs_ui.mjs` and `tests/test_web_contract.py`:
1. Desktop (`1440x900` and `1200x800`): clicking a job card opens `#appInspector` (`#insBody` visible, `#jobsDetailDrawer` NOT `.open`, no modal backdrop blocking `#jobsMapCanvas`!), shows `↑ / ↓` step controls, evidence (`ats_provider`, `last_verified_at`, `>14 dias` stale warning when applicable), TfNSW Rockdale commute windows or `"Trajeto não verificado"`, ODbL attribution, notes textarea, favorite & applied/contact buttons.
2. Collapsing `#appInspector` (via `Fechar` button or `I` shortcut) hides `#appInspector` cleanly and allows re-opening via card click or `I`.
3. Mobile (`390x844`): `#appInspector` remains hidden, tapping a card opens `#jobsDetailDrawer` bottom sheet with identical detail/actions/notes/outreach functionality and `0` horizontal overflow (`scrollWidth <= clientWidth`).
4. Switching back to `Stays` still renders `renderStaysInspector()` normally without layout regression.

- [ ] **Step 2: Run RED**

Run: `node tests/test_jobs_ui.mjs`  
Expected: FAIL because desktop currently opens `#jobsDetailDrawer` instead of `#appInspector` / `#insBody`.

- [ ] **Step 3: Implement unified `renderJobsInspector()` & responsive container routing**

- Extract pure HTML builders `buildJobDetailHtml(job, items)` and `buildJobDetailActionsHtml(job)` using `SydneyJobsV5.buildJobDetailModel`.
- Add desktop breakpoint helper `isDesktopJobsInspectorViewport() => window.matchMedia('(min-width: 1100px)').matches`.
- Update `atlas.css`:
  - Remove blanket `body:not(.ws-stays) #appInspector { display: none; }` block overlap by permitting `body.ws-jobs #appInspector:not(.collapsed)` on `@media (min-width: 1100px)` (`width: clamp(350px, 28vw, 410px)`, `flex-shrink: 0`), with `.content-area { min-width: 0; }` so `.jobs-list-pane` + `.jobs-map-pane` fit cleanly without horizontal overflow.
  - Hide `#appInspector` on `< 1100px` when `body.ws-jobs`, and style `#jobsDetailDrawer` as a clean slide-up bottom sheet on `<= 850px` (`border-radius: 14px 14px 0 0; max-height: 86dvh;`).
- Update `switchWorkspace`, `selectJob`, `renderJobs`, `toggleInspector`, and resize listener (`matchMedia` `change` event):
  - Keep `#insBody` synced with `renderJobsInspector()` on desktop, `#jobsDetailDrawer` synced on `< 1100px`, call `JobsMap.resize()` after inspector open/close transitions (`220ms`), and close `#jobsDetailDrawer` if resized up to desktop (re-rendering into `#insBody` if `selectedUid` is still valid).
- Rebuild `index.html`.

- [ ] **Step 4: Run GREEN**

Run: `python -m pytest -q tests/test_web_contract.py` and `node tests/test_jobs_ui.mjs`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sydney-hub/web/template.html sydney-hub/web/atlas.css sydney-hub/web/index.html sydney-hub/tests/test_jobs_ui.mjs sydney-hub/tests/test_web_contract.py
git commit -m "feat(jobs-v5): consolidate Inspector on desktop and bottom sheet on mobile"
```

---

### Task 6: Loading Skeletons, Error States, Selection/Pagination Guards, Keyboard & A11y Hardening

**Files:**
- Modify: `sydney-hub/web/template.html` (`renderJobsList` skeleton, `renderJobs` stale-selection reset, keyboard `ArrowDown`/`ArrowUp`/`j`/`k`/`i`/`Escape` + tablist arrow navigation, focus restoration on drawer/dialog close)
- Modify: `sydney-hub/web/atlas.css` (skeleton shimmer/bars respecting `prefers-reduced-motion`, `:focus-visible` WCAG AA+ contrast in dark & light themes, touch targets `>= 40px` mobile)
- Rebuild: `sydney-hub/web/index.html`
- Test: `sydney-hub/tests/test_jobs_ui.mjs`

**Interfaces:**
- Produces clean accessible states (`aria-busy` skeleton, `role="alert"` / `role="status"` API failure retry, neutral Inspector placeholder when no card is selected in the active segment/page, keyboard `ArrowLeft`/`ArrowRight` across `#jobsSegmentTabs`).

- [ ] **Step 1: Write failing Playwright accessibility & edge-case tests**

Extend `sydney-hub/tests/test_jobs_ui.mjs` with tests covering:
1. **Loading skeleton & API failure + retry:** Delayed `/api/opportunities` renders `.jobs-skeleton-card` (`aria-busy="true"`), HTTP `503` renders `"Catálogo indisponível"` + retry recovery.
2. **Stale selection guard on segment/filter/pagination change:** Selecting an item in `Verified Jobs`, then switching to `Employer Leads` (or applying a filter that removes it) resets `selectedUid` cleanly and clears stale detail content without erasing `state.jobFavorites`, `state.jobNotes`, or `state.jobStatus`.
3. **Keyboard navigation & focus:** `ArrowLeft`/`ArrowRight` switches tabs in `#jobsSegmentTabs`; `ArrowDown`/`ArrowUp` moves selection in active list; `I` toggles `#appInspector`; `Escape` closes `#jobsDetailDrawer` on mobile and restores focus to trigger card; zero `pageerror` and zero horizontal overflow on desktop & mobile.

- [ ] **Step 2: Run RED**

Run: `node tests/test_jobs_ui.mjs`  
Expected: FAIL on missing `.jobs-skeleton-card` and segment keyboard navigation assertions.

- [ ] **Step 3: Implement states, selection guard, keyboard & accessibility**

- Add lightweight structural skeleton cards (`aria-busy="true"`) while `jobsLoadState === 'loading'` (respecting `prefers-reduced-motion: reduce`).
- Render neutral helpful Inspector state when `body.ws-jobs` is active and no item is selected (or when selection leaves filtered results).
- Add roving focus / `ArrowLeft`/`ArrowRight` support to `#jobsSegmentTabs` and focus-return tracking on `#jobsDetailDrawer` close.
- Verify button contrast, `aria-label`s, and `min-height >= 40px` touch targets on `< 850px`.
- Rebuild `index.html`.

- [ ] **Step 4: Run GREEN**

Run: `node tests/test_jobs_ui.mjs`  
Expected: ALL assertions PASS.

- [ ] **Step 5: Commit**

```bash
git add sydney-hub/web/template.html sydney-hub/web/atlas.css sydney-hub/web/index.html sydney-hub/tests/test_jobs_ui.mjs
git commit -m "feat(jobs-v5): add skeletons, selection guards, and keyboard accessibility"
```

---

### Task 7: Impeccable Visual Review (`critique`/`polish`/`audit Jobs`), Full Verification Gate & Pilot Copy Smoke Test

**Files:**
- Inspect & refine (only if Impeccable audit/detector finds visual/a11y flaws scoped strictly to Jobs): `sydney-hub/web/template.html`, `sydney-hub/web/atlas.css`, `sydney-hub/web/index.html`

- [ ] **Step 1: Run Impeccable mechanical detector and visual audit workflow (scoped strictly to Jobs; do NOT run `npx impeccable update`)**

- Run: `& "C:\Users\guicx\.gemini\config\skills\impeccable\scripts\impeccable.cmd" detect --json sydney-hub/web/template.html sydney-hub/web/atlas.css`
- Perform Impeccable `critique Jobs`, `polish Jobs`, and `audit Jobs` passes against rendered desktop (`1440x900`, `1200x800`) and mobile (`390x844`) views in dark & light themes, verifying Stays/Home/Tracker/Sources remain untouched.
- If edits are made, rebuild `sydney-hub/web/index.html` via `python web/build_dashboard.py` and re-run detector + Playwright.

- [ ] **Step 2: Run full automated regression suites**

- `python -m pytest -ra` (in `sydney-hub/`) — expect 0 failures, all existing V1–V4 + new V5 tests passing.
- `node --test tests/test_jobs_v5_engine.mjs` — expect 0 failures.
- `node tests/test_jobs_ui.mjs` — expect exit `0`.

- [ ] **Step 3: Read-only smoke test against an isolated temporary copy of Entrega 4 `hub.db`**

- Copy `C:\Users\guicx\.codex\worktrees\sydneystays-employer-pilot\career-ops\sydney-hub\data\hub.db` to a temporary directory (`tempfile.TemporaryDirectory`), start local FastAPI on an ephemeral port (`127.0.0.1`), verify `GET /api/opportunities`, `GET /api/opportunities/summary`, and Playwright headless desktop + mobile rendering against those 155 real `employer_lead` records (`0` `verified_job` empty state + switching to `Employer Leads` rendering all 155 cards & map pins with `0` `pageerror`). Delete temp copy cleanly afterwards.

- [ ] **Step 4: Independent Final Review & Commit**

- Commit final Jobs polish changes (if any) on `codex/sydneystays-jobs-v5-ux`, verify `C:\Users\guicx\.codex\worktrees\sydneystays-employer-pilot\career-ops` and its `hub.db` remain completely untouched, run an independent final review over `d1d08591..HEAD`, and present results to user for merge/next-step decision (NO merge, NO push, NO deploy).
