# SydneyStays — Backpacker Published Jobs Engine (Entrega 4)

**Data:** 2026-11-14  
**Estado:** Design proposto; decisões do piloto respondidas, requer aprovação antes do plano de implementação
**Estratégia escolhida pelo usuário:** C — fontes híbridas, ativadas em etapas  
**Base:** Entregas 1–3 na branch local `codex/sydneystays-employer-pilot`

## 1. Intenção e diagnóstico

O SydneyStays precisa encontrar anúncios reais e recentes para backpackers na Austrália, inclusive de empregadores ausentes do inventário OSM. O piloto anterior terminou com 155 `employer_lead` e zero `verified_job`; isso mede o alcance limitado do caminho empregador → website → ATS, não a inexistência de trabalho acessível a partir de Rockdale.

Hoje o `AtsBridge` reutiliza `providers/*.mjs` do Career Ops, mas recebe um URL de board encontrado no website de um empregador. `ats_filter.py` aceita apenas Rockdale, Kogarah, Hurstville e Mascot. `CatalogRepository` já distingue `verified_job`, `unverified_job_post` e `employer_lead` e exige evidência `job_listing` com `supports_job=1` e `checked_at` para expor um anúncio como verificado. `/api/opportunities` já aceita filtros, mas a tabela `opportunity_commutes` não guarda horário de partida; o Jobs Explorer preserva estado do usuário pelo ID de oportunidade.

## 2. Metas, limites e anti-metas

### Metas

- Descobrir anúncios diretamente por profissão e região, sem depender do OSM.
- Priorizar barista, bartender/bar staff, kitchen hand, waitstaff, hospitality all-rounder, cleaner, housekeeping/room attendant, retail assistant e warehouse picker/packer, com sinônimos determinísticos.
- Separar **descoberta**, **verificação de evidência**, **deduplicação** e **compatibilidade**. Uma falha numa fonte não deve bloquear as outras.
- Expandir a busca para uma região configurável com Rockdale como origem inicial; o limite de 45 minutos por transporte público só se aplica quando destino, horário e dado de rota permitem calculá-lo.
- Preservar URLs originais, proveniência, `last_verified_at`, dados desconhecidos, IDs existentes, favoritos, notas, buscas salvas, status de candidaturas e o design Jobs atual.

### Fora do escopo desta entrega

- Novos crawlers de Google Maps/OSM e aumento da coleta de empregadores.
- Avaliação automática de elegibilidade para WHV 462 ou para 88 dias. Sem evidência explícita, `visa_status` continua `unknown`.
- Submissão de candidaturas, coleta recorrente ativada automaticamente, upgrade do Career Ops ou redesign da interface.
- Integração produtiva de Facebook antes de uma revisão separada de acesso, retenção e termos. O canal existente continua independente.

### Anti-metas

- Não inferir que um anúncio está encerrado porque sumiu de uma página de busca, houve 403, timeout ou falha de rede.
- Não apresentar uma página genérica de carreiras, lead OSM ou resultado textual sem anúncio específico como vaga verificada.
- Não transformar remuneração estimada pela plataforma em salário anunciado; não inventar experiência, modalidade, certificação, visto, endereço ou trajeto.
- Não usar listas de empresas de terceiros sob licença não comercial em um produto comercial sem autorização.

## 3. Abordagens consideradas

| Abordagem | Vantagem | Limite | Decisão |
| --- | --- | --- | --- |
| A. ATS com descoberta independente de boards | Reaproveita o bridge e anúncios oficiais; custo marginal baixo | APIs por board não fornecem busca global por profissão; descoberta de boards e limites de taxa são trabalho contínuo; pequenos negócios ficam de fora | Parte do híbrido |
| B. Adzuna AU | Busca direta por cargo e localidade; potencial para vagas casuais fora do inventário | Credenciais, quota/licença, atribuição, duplicatas e descrição parcial; cobertura AU não medida nesta sessão | Experimento condicionado a acesso e termos |
| C. Híbrido: ATS, fontes autorizadas, Facebook futuro e Employer Discovery | Diversidade e isolamento de falhas; usa cada fonte para o que ela oferece | Exige política de fonte, telemetria e deduplicação rigorosas | **Escolhida** |

O código de `scan-ats-full.mjs` pode inspirar a descoberta, mas suas listas externas de empresas vêm de `Riley-Dorrington/job-board-aggregator/data`, declaradas **CC BY-NC 4.0**. A licença MIT cobre o código, não esses dados. O SydneyStays só os usará se obtiver permissão compatível. Não há `portals.yml` AU neste worktree para servir de seed inicial. O piloto deverá montar uma lista pequena de boards a partir de **páginas oficiais de carreiras descobertas independentemente do OSM**, registrar para cada seed o URL da página que aponta para o board e a data de checagem, e verificar permissões antes de consultar a API do ATS. Índices/feeds de primeira parte também podem fornecer seeds quando seus termos permitirem. Nenhuma inferência a partir de nomes de empresas da Adzuna será usada para consultar ATS terceiros. A lista é resultado auditável do piloto, não pré-condição fornecida pelo usuário. [Licença do dataset](https://github.com/Riley-Dorrington/job-board-aggregator#license)

## 4. Arquitetura escolhida

```text
Adzuna AU (se autorizada) ──┐
ATS / feeds permitidos ─────┼─→ observação do anúncio → evidência → identidade/dedup
Facebook (fase posterior) ──┘                                  │
OSM / websites ───────────────→ leads de empregadores          ▼
                                                filtro de cargo + local + trajeto
                                                                │
                                                   SQLite → FastAPI → Jobs
```

### 4.1 Registro de fontes e adaptadores independentes

Cada fonte declara: identificador, modo de acesso, escopo permitido, atribuição, política de retenção, quota, credenciais, limites de pedidos, custo e estado de saúde. Cada adaptador produz observações com `source_listing_id` (se fornecido), `source_url` exato, empregador e cargo como publicados, localização bruta, `published_at` se fornecido e `observed_at`. O adaptador não decide `verified_job` nem compatibilidade.

O primeiro caminho implementável sem novo contrato é a leitura de boards ATS públicos **já descobertos de forma independente**: reutilizar o bridge Node e os provedores existentes. Greenhouse, Lever e Ashby documentam interfaces de anúncios publicados por board. Descoberta de slugs por dataset externo depende de licença; lista de boards próprios/configurados e links de websites oficiais são admissíveis, sujeitos à política de cada provedor. [Greenhouse](https://docs.greenhouse.io/job-board) · [Lever](https://github.com/lever/postings-api/blob/master/README.md) · [Ashby](https://developers.ashbyhq.com/docs/public-job-posting-api)

**Fonte direta inicial candidata:** o feed XML de primeira parte da Workable inclui vagas publicadas no Jobs by Workable, com ID, título, data, URL e localização, sem seed de empresa. A própria Workable o descreve como feed programático para job boards/parceiros e diz que é grande e atualizado a cada hora. O piloto deve ler o XML em streaming, com limite de bytes/pedidos, manter o URL original e confirmar condições de publicação antes de exibi-lo no catálogo. Uma coleta manual por dia basta para medir cobertura; nenhuma busca por empresa OSM é necessária. [Workable XML feed](https://help.workable.com/hc/en-us/articles/4420464031767-Utilizing-the-XML-Job-Feed)

Adzuna é um segundo caminho, **não** um insumo para consultar diretamente provedores de anúncios terceiros: seus termos exigem atribuição, podem exigir licença para uso comercial continuado e restringem esse tipo de consulta. O usuário possui uma conta, mas isso **não comprova** autorização para publicação comercial. Primeiro validar localmente credenciais, quota AU, cobertura e permissão para o uso pretendido, sem expor chaves no git ou em relatórios. A avaliação no período de teste fica isolada do catálogo público até que a publicação/retenção esteja autorizada. Não comprar plano ou exceder a quota gratuita: teto externo confirmado de **AU$0**. Salários com `salary_is_predicted` são estimativas, nunca `pay_display` de vaga. [Documentação](https://developer.adzuna.com/docs/search) · [Termos](https://developer.adzuna.com/docs/terms_of_service)

Facebook mantém adaptador e execução separados; não é pré-requisito para o primeiro piloto. Seu uso produtivo exige revisão específica das permissões disponíveis para o motor atual e da forma como posts e contatos são armazenados.

### 4.2 Evidência, estado e frescor

O pipeline de verificação aceita `verified_job` apenas com **anúncio específico acessível na fonte permitida**, título real, URL persistível, `source_type='job_listing'`, `supports_job=1`, `checked_at` e origem indicada. Um anúncio encontrado, mas sem checagem suficiente, é `unverified_job_post`. OSM sem anúncio é `employer_lead`. `last_verified_at` registra a última observação bem-sucedida, nunca a data de publicação. Exibir também a fonte da verificação (board ATS, Adzuna ou website oficial), para não sugerir confirmação pelo empregador quando a evidência veio de um portal.

Atualização incremental por ID da fonte e URL canônica; observações repetidas são idempotentes. Ausência em uma busca, bloqueio ou timeout marca somente `check_outcome` e saúde da fonte; não apaga nem fecha a vaga. Expiração exige sinal explícito da própria fonte ou regra de rechecagem documentada; anúncio sem confirmação recente aparece como **evidência antiga**, não como comprovadamente encerrado. A URL original e os IDs locais permanecem estáveis. Deduplicação forte usa ID da fonte e URL canônica; fusão entre fontes usa empregador+cargo+localização apenas como proposta auditável, sem colapsar vagas distintas por turno ou unidade.

**Regra de frescor do piloto:** `recent` significa `published_at` fornecido pela fonte nos últimos 30 dias e anúncio verificado novamente nas últimas 48 horas. Um anúncio sem data de publicação pode aparecer como verificado com `published_at=unknown`, mas não conta para a meta de 20 vagas recentes nem recebe rótulo “recente”. Um anúncio publicado há mais de 30 dias pode permanecer visível se ainda estiver acessível, com a data explícita. O badge atual de evidência antiga após 14 dias continua válido fora do fechamento do piloto.

### 4.3 Relevância e região

Buscar inicialmente na região metropolitana de Sydney com termos por família de cargo, paginação e limite por fonte. A classificação de profissão usa sinônimos e exclusões determinísticas; resultados desconhecidos ficam para revisão, sem chamada LLM no caminho comum. `casual`, `part-time`, `temporary`, `seasonal`, experiência, RSA/RCG, remuneração e WHV são campos de evidência: ausência não vira compatibilidade. Sinais suspeitos reduzem prioridade e exigem revisão, sem acusação automática.

Localização é tratada em três níveis: (1) endereço/coordenadas confirmadas do anúncio; (2) subúrbio/postcode explícito, mas ponto de chegada ainda aproximado; (3) genérica/desconhecida. Somente (1), com uma rota real calculada e registrada, pode satisfazer o filtro estrito `<=45 min`. Os níveis (2) e (3) continuam visíveis como `commute_unknown`, fora desse filtro, sem excluir o anúncio do catálogo.

**Decisão confirmada para o piloto:** duas janelas de referência configuráveis, terça-feira às 09:00 e sexta-feira às 18:00 (horário Sydney), com origem Rockdale. Cada duração fica ligada à janela, ao serviço/dataset e à data da consulta; a atual tabela `opportunity_commutes` (PK por oportunidade+origem) deve ser migrada sem reinterpretar linhas antigas como medições por horário. Uma rota em uma janela não garante duração em outra nem compatibilidade com o horário real do turno. Com teto **AU$0**, a primeira opção a verificar é a **Trip Planner API da Transport for NSW**, cujo plano Bronze padrão é gratuito, exige chave e documenta quota de 60.000 chamadas/dia e limite de 5/s; se não houver chave ou rota confiável, manter `commute_unknown` e registrar que a meta de 10 trajetos não passou. GTFS com cálculo local é alternativa, não obrigação inicial. Não criar tempos por distância em linha reta nem ativar uma API paga. [TfNSW Trip Planner](https://opendata.transport.nsw.gov.au/dataset/trip-planner-apis) · [Limites e autenticação](https://opendata.transport.nsw.gov.au/developers/api-basics)

### 4.4 Integração e operação

FastAPI lê o catálogo SQLite existente; filtros por profissão, fonte, estado, modalidade e deslocamento passam a refletir o recorte regional e a precisão do trajeto. A interface mantém cartões, drawer e armazenamento local; apenas rótulos e filtros necessários à distinção de evidência/commute são acrescentados. Falha da API mantém erro visível, sem fallback estático silencioso.

O piloto roda manualmente, com limites de páginas/pedidos, backoff em 429/5xx, logs sem credenciais, orçamento por fonte e relatório de saúde. APScheduler permanece no stack, mas nenhuma coleta recorrente é ativada nesta entrega. Segredos ficam fora do git. Migrações SQLite exigem backup íntegro e rollback comprovado antes da primeira escrita real.

## 5. Piloto e critérios de aceitação

**Duração:** sete dias de observação controlada, sem agendamento recorrente. **Escopo:** cargos acima, região de Sydney, Rockdale como origem. **Gate de fontes:** uma fonte só entra no catálogo exibido se acesso, armazenamento, atribuição e custos estiverem aprovados; caso contrário, sua avaliação fica separada. Nenhum serviço pago é ativado sem teto de custo definido pelo usuário. Comparar yield por fonte, cargo e modalidade, inclusive `verified_job / pedido` e custo por vaga única.

O piloto só passa se, numa execução reproduzível:

1. Encontrar **pelo menos 20 anúncios únicos, específicos, recentes e verificáveis** conforme a regra de 30 dias/48 horas, dos quais **pelo menos 10** tenham trajeto medido `<=45 min` em uma janela de referência identificada; **pelo menos 5** anunciem explicitamente `casual`, `part-time`, `temporary` ou `seasonal`. Metas propostas para medir ganho sobre zero, não garantia de mercado.
2. Fazer auditoria manual de **100% dos anúncios rotulados `verified_job`**: título, empresa, URL de anúncio, origem, localização e evidência concordam; nenhum lead, página genérica ou anúncio sem checagem é promovido. Pelo menos **95%** dos links auditados permanecem válidos no momento da revisão.
3. Reportar anúncios brutos, únicos, duplicatas e eventuais colisões indevidas por fonte; **0 fusões comprovadamente incorretas** na amostra auditada. Indicar `published_at` e `last_verified_at` separadamente; **0 campos presumidos** de salário, requisitos, visto ou trajeto.
4. Registrar para cada fonte pedidos, falhas, rate limits, tempo, credenciais necessárias, custo real e licença/atribuição aplicada. Respeitar orçamento configurado para o piloto; custo comercial da Adzuna fica `unknown` até oferta/licença da conta.
5. Passar testes unitários de normalização e classificação, integração de cada adaptador e do catálogo/rollback, regressão FastAPI e Playwright desktop/mobile. Testes de falha demonstram que queda de uma fonte não altera outra e que timeout não encerra vaga.

Se a permissão da Adzuna, as janelas de trajeto ou os volumes reais impedirem os limiares, publicar métricas e bloqueios; não declarar a Entrega 4 concluída. Os 155 leads existentes permanecem no catálogo e não contam para as metas de vagas publicadas.

## 6. Sequência de implementação após aprovação

1. Política de fonte, modelo de observação e testes de deduplicação/evidência; backup e migração reversível.
2. Descoberta direta no feed Workable permitido e em boards ATS de origem registrada, reutilizando o bridge existente, com busca regional e medição de yield.
3. Avaliação Adzuna AU em modo segregado; ativação de catálogo apenas após confirmação contratual e testes.
4. Roteamento TfNSW dentro da quota gratuita, com observações por janela e `commute_unknown` honesto; GTFS local apenas se a API não for viável.
5. Filtros FastAPI/Jobs, testes end-to-end e piloto manual auditado.

**Decisões deixadas ao implementador:** nomes internos de módulos e índices SQLite, escolha de fixtures, formato do relatório e parâmetros de rate limiting dentro das quotas gratuitas aprovadas. **Gates externos:** a conta Adzuna só habilita uma avaliação gratuita e segregada após verificar seus limites; publicação no catálogo exige permissão contratual correspondente. Nenhuma fonte ou rota paga é autorizada neste piloto.
