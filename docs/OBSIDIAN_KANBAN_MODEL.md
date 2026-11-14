# Modelo Obsidian + Kanban — JOB//HUNTER

**Decisão de produto:** o vault Obsidian será a base de dados legível/editável pelo usuário para o fluxo JOB//HUNTER. Cada vaga/aplicação será uma nota Markdown com propriedades YAML; quadros Kanban serão vistas dessas notas, não uma segunda base. O Hermes continua responsável por executar agentes e tarefas. O Obsidian não substitui os mecanismos Hermes nem as regras de integridade do Career-ops.

**Estado:** modelo proposto para orientar a interface e a integração. Ainda não há caminho de vault configurado neste ambiente (`OBSIDIAN_VAULT_PATH` vazio e o caminho padrão não encontrado). Este documento não migra dados, não cria um vault e não altera o tracker existente.

## Princípios

1. **Uma nota por vaga/requisição**: descoberta, análise, candidatura, entrevistas e resultado atualizam a mesma nota. Não crie uma cópia por etapa.
2. **O vault é a fonte de verdade do JOB//HUNTER** para o lifecycle da vaga e o histórico de recrutamento criado pela nova aplicação.
3. **Markdown/YAML primeiro**: o app pode ler e gravar arquivos no vault por um adaptador local validado. Não introduzir SQLite como outra autoridade do lifecycle. SQLite existente pode continuar servindo os fluxos que já dependem dele até haver migração deliberada e verificada.
4. **Integração com Career-ops sem quebrar seu contrato**: o tracker do Career-ops continua regido pelo contrato atual (`data/applications.md` é canônico para esse sistema). Começar com ingestão/leitura para o vault; qualquer escrita bidirecional requer um adaptador explícito, idempotente e testes de conflito. Não editar o tracker manualmente nem presumir que os dois status sincronizam.
5. **Nada de informação inventada**: campos ausentes ficam vazios/nulos; análise e WHV carregam evidência, origem e confiança quando disponíveis.
6. **O status da vaga não é status da execução**: uma vaga pode estar `ANALYZED` enquanto uma execução de CV está `COMPLETED`. São objetos e quadros distintos.

## Quadro 1 — Pipeline de vagas e candidaturas

Fluxo principal, ordenado da esquerda para a direita:

| Coluna Kanban | Propriedade `status` | Critério de entrada | Próximo passo esperado |
|---|---|---|---|
| Descoberta | `DISCOVERED` | Fonte e URL original registrados; deduplicação concluída | Verificar legitimidade, duplicata e dados básicos |
| Triagem | `SCREENING` | Vaga ainda precisa de leitura/validação | Confirmar empresa, localização, elegibilidade e requisitos |
| Analisada | `ANALYZED` | Avaliação concluída, com resultado e evidências | Decidir se vale preparar candidatura |
| Pronta para candidatura | `READY_TO_APPLY` | CV/documentos revisados; falta ação humana de envio | Revisão final explícita pelo usuário |
| Candidatura enviada | `APPLIED` | Envio confirmado pelo usuário | Aguardar resposta e calcular follow-up |
| Em análise | `IN_REVIEW` | Empresa confirmou ou candidatura está em avaliação | Acompanhar contato e prazos |
| Entrevista | `INTERVIEW` | Processo de entrevista confirmado | Preparar rodada e registrar datas/notas |
| Oferta | `OFFER` | Oferta recebida e verificada | Avaliar condições e registrar decisão |
| Encerrada | `CLOSED` | Processo terminou | Definir `outcome`: `HIRED`, `REJECTED`, `WITHDRAWN`, `IGNORED` ou `EXPIRED` |

`CLOSED` é a coluna terminal; o campo `outcome` distingue o motivo. Não misture `IGNORED`/`EXPIRED` com rejeição pela empresa.

### Regras de movimentação

- Mudar para `APPLIED` só depois da confirmação humana de que a candidatura foi enviada; gerar CV ou abrir formulário não conta como envio.
- `INTERVIEW` e `OFFER` exigem evidência humana (mensagem/invite/offer) ou anotação explícita, nunca inferência de IA.
- `REJECTED`, `WITHDRAWN`, `HIRED`, `IGNORED` e `EXPIRED` fecham o fluxo; preservar histórico, não apagar a nota.
- Uma execução que falha não muda automaticamente o estágio da vaga.
- Todas as ações externas (enviar candidatura/e-mail, upload ou publicação) permanecem sujeitas à revisão e aprovação humana.

## Quadro 2 — Execuções e aprovações

Este quadro é para tarefas do sistema, vinculado à vaga por `job_id`; não substitui o Quadro 1.

| Coluna | `execution_status` | Definição |
|---|---|---|
| Fila | `QUEUED` | Ação validada, ainda não iniciada |
| Em execução | `RUNNING` | Processo real iniciado; exibir runtime e ID da execução |
| Revisão necessária | `NEEDS_REVIEW` | Saída produzida; usuário precisa revisar |
| Aprovação pendente | `AWAITING_APPROVAL` | Ação externa aguarda confirmação explícita |
| Concluída | `COMPLETED` | Resultado observado e artefato/link persistido |
| Falhou | `FAILED` | Erro confirmado, com mensagem segura e log/referência |
| Interrompida | `CANCELLED` | Cancelamento confirmado pelo executor; pedido de cancelamento não basta |

Tipos previstos: `SCAN`, `ANALYZE`, `TAILORED_CV`, `COVER_LETTER`, `FOLLOW_UP_DRAFT`, `TRACKER_SYNC`, `HERMES_RUN`. Não exibir execução/agent como disponível até o backend confirmar runtime, origem e estado.

## Estrutura inicial do vault

```text
JOB-HUNTER/
  Dashboard.md
  Boards/
    Applications.md
    Executions.md
  Jobs/
    <job-id>.md
  Runs/
    <run-id>.md
  Templates/
    Job.md
    Run.md
  Sources/
    Search-profiles.md
```

O quadro no JOB//HUNTER será uma vista derivada das propriedades YAML dos arquivos. No Obsidian, o usuário poderá abrir/editar as notas diretamente; uma vista Kanban dentro do Obsidian exige plugin ou implementação que leia e grave o mesmo `status`. Não assumir que o plugin Kanban padrão sincroniza propriedades YAML: validar esse comportamento antes de adotá-lo, ou manter o quadro Kanban no app e usar o Obsidian como editor/base Markdown.

## Modelo de nota de vaga

```yaml
---
id: "job-<stable-id>"
type: job
status: DISCOVERED
outcome: null
title: ""
company: ""
location: ""
source: ""
source_url: ""
req_id: ""
discovered_at: "YYYY-MM-DD"
published_at: null
salary_min: null
salary_max: null
salary_currency: ""
whv_eligibility: unknown
fit_score: null
fit_evidence: []
career_ops_report: ""
cv_artifact: ""
follow_up_at: null
created_at: "YYYY-MM-DDTHH:mm:ssZ"
updated_at: "YYYY-MM-DDTHH:mm:ssZ"
---

## Descrição original

<!-- Arquivar texto apenas via fluxo autorizado; manter URL e fonte. -->

## Análise

## Histórico
- YYYY-MM-DD — DISCOVERED — Fonte: ...
```

**Identidade/deduplicação:** preferir a chave URL normalizada e preservar `req_id` quando disponível. Não deduplicar somente pelo título/empresa quando houver IDs diferentes. Gerar `id` estável a partir da identidade de origem usando a rotina existente; não mudar a URL original.

**Vocabulário fechado:** usar os status das colunas e `outcome` acima; não criar variações livres em campos usados pelos quadros. `unknown` é permitido para informação não verificada (por exemplo, `whv_eligibility`). Valores `null`/vazios significam dado ausente, não negativo.

## Modelo de nota de execução

```yaml
---
id: "run-<runtime-id>"
type: execution
execution_status: QUEUED
kind: ANALYZE
job_id: "job-<stable-id>"
engine: "career-ops|hermes|maestri|python-service"
agent: ""
started_at: null
finished_at: null
approval_required: false
approval_status: not_required
result_uri: ""
error_summary: ""
---

## Resultado

## Eventos
- YYYY-MM-DDTHH:mm:ssZ — QUEUED — origem: ...
```

Não guardar tokens, credenciais, conteúdo sensível do candidato ou logs integrais com segredos em propriedades/notas sincronizadas. Armazenar no vault apenas referências seguras a artefatos/logs com acesso controlado.

## Mapeamento inicial de status existentes

| Status atual | Status no quadro | Observação |
|---|---|---|
| `Evaluated` | `ANALYZED` | Preservar link do relatório e score existente |
| `Applied` | `APPLIED` | Envio já registrado |
| `Responded` | `IN_REVIEW` | Registrar o tipo de resposta em histórico/notas |
| `Assessment` | `IN_REVIEW` | Avaliação/etapa assíncrona da empresa; registrar como atividade |
| `Interview` | `INTERVIEW` | Preservar datas e rodada |
| `Offer` | `OFFER` | Não inferir aceite |
| `Hired` | `CLOSED` + `outcome: HIRED` | Terminal positivo |
| `Rejected` | `CLOSED` + `outcome: REJECTED` | Rejeição pela empresa |
| `Discarded` | `CLOSED` + `outcome: WITHDRAWN` ou `IGNORED` | Escolher segundo histórico; nunca converter automaticamente sem evidência |
| `SKIP` | `CLOSED` + `outcome: IGNORED` | Vaga descartada por decisão do candidato/sistema |

Mapeamento é proposta de visualização, não migração automática. Os arquivos fonte atuais devem permanecer intactos até revisão de duplicatas, resolução de identidade e prova de round-trip.

## Fases de adoção

1. **Desenhar e revisar** os campos, quadros e mapeamento de status (este documento).
2. **Localizar o vault** e confirmar pasta-base; não gravar antes disso.
3. **Protótipo somente de leitura**: importar/visualizar uma amostra e apresentar conflitos sem sobrescrever.
4. **Teste de ida e volta**: preservar URLs, IDs, notas, links de relatório, status e históricos; validar casos ambíguos manualmente.
5. **Ativar gravações** no vault pelo backend local, com gravação atômica, validação de schema, backup e leitura de confirmação.
6. **Integrar Career-ops** via adaptador explícito; até essa integração ser testada, o tracker nativo do Career-ops continua sendo sua fonte oficial e o vault é fonte de verdade para os novos registros do JOB//HUNTER.

## Critérios de aceite do modelo

- Uma única nota por vaga, com URL original e identidade estável.
- A coluna de vaga não muda por falha de execução.
- Uma candidatura não aparece como enviada sem confirmação humana.
- Os quadros são vistas dos registros Markdown e não cópias independentes.
- A importação inicial é não destrutiva e lista conflitos para revisão.
- Nenhuma credencial ou sucesso simulado aparece no vault/UI.
