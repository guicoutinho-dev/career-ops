# consult-chatgpt — consultoria técnica via ChatGPT no portal do Maestri

Sem API da OpenAI, sem Codex, sem serviços pagos, sem cookies/tokens. Usa apenas `maestri portal ...`
com a sessão que o usuário já abriu no portal dedicado `ChatGPT_Consultas`. Cada consulta abre um **chat temporário**
(`https://chatgpt.com/?temporary-chat=true`), independente de qualquer conversa ou projeto.

## Uso

```powershell
# 1. Mostra a pergunta, valida e imprime o hash de aprovação (exit 2, nada é enviado)
node consult-chatgpt.mjs "pergunta técnica"
node consult-chatgpt.mjs --file consultas/pergunta.md

# 2. Após o HUMANO ler a pergunta e aprovar, envia com o hash daquela pergunta
node consult-chatgpt.mjs "pergunta técnica" --approve <hash>
```

Opções: `--portal NOME` (padrão `ChatGPT_Consultas`), `--no-stop`, `--timeout S` (120), `--stable MS` (3000), `--poll MS` (1000),
`--dir PASTA` (`consultas`), `--dry-run`, `--json`, `--resend`.

- **stdout:** somente o texto da resposta (ou JSON `{id,state,file,response}` com `--json`). Mensagens vão para stderr.
- **Arquivos** (nunca sobrescritos): `consultas/<id>.md` (pergunta, resposta, metadados) e `consultas/<id>.json`
  (estado, hash, timestamps, estratégia de leitura, diagnóstico — sem texto da pergunta).
- **Códigos de saída:** 0 ok · 1 uso · 2 aprovação exigida/inválida · 3 conteúdo bloqueado · 4 portal/seletor/login/CAPTCHA ·
  5 timeout (parcial salvo) · 6 resposta vazia/incompleta/ambígua · 7 envio incerto (não reenviado) · 130 interrompido.

## Segurança

- Aprovação **por pergunta**: o hash SHA-256 (12 hex) do texto normalizado. Mudou uma letra → novo hash → nova aprovação.
  Em terminal interativo o script pede a digitação do hash. `--approve` só deve ser usado depois que o humano viu a pergunta.
- Bloqueios (nada é enviado, achados mostrados só por categoria): arquivos-fonte proibidos (`cv.md`, `config/profile.yml`,
  `.env`, `modes/_profile.md`, `data/`, `output/`, `reports/`, ...), e-mails, telefones, chaves/JWT/Bearer, chave privada,
  cookies, senhas, segredos longos, caminhos pessoais, valores de campos do perfil e linhas do CV/article-digest.
  A detecção é heurística: **a revisão humana continua obrigatória**.
- CAPTCHA/verificação: o script aborta; não há tentativa de contornar.
- Sem reenvio automático. Se o envio ficou incerto, execuções seguintes com a mesma pergunta são recusadas
  (exit 7) a menos que `--resend`.
- Falha → modo manual: cole a pergunta de `consultas/<id>.md` no ChatGPT e salve a resposta em
  `consultas/<id>.resposta-manual.md`.

## Como o Antigravity_Mestre usa (sem alterar os outros terminais)

1. Redige a pergunta técnica **sem dados pessoais** em `consultas/pergunta-<tema>.md`.
2. `node consult-chatgpt.mjs --file consultas/pergunta-<tema>.md` → mostra o texto e o hash ao usuário.
3. O usuário aprova → `... --approve <hash> --json` (comando de até ~2 min; para respostas longas use `--timeout`).
4. Lê `response` (stdout) ou `consultas/<id>.md`, avalia e, se útil, repassa o resultado ao terminal responsável
   com `maestri ask "Farol" "<resumo + caminho de consultas/<id>.md>"`.
5. Os demais agentes **não** consultam o ChatGPT sozinhos; só o coordenador dispara.

## Testes

`node --test tests/consult-chatgpt.test.mjs` — usa `tests/fake-maestri.mjs` (CLI falso via `CONSULT_MAESTRI_CMD`),
sem rede. Cobre aprovação, bloqueios, resposta curta/longa, timeout, interrupção, falha de seletor, login/CAPTCHA,
campo divergente, resposta vazia/incompleta/ambígua, envio incerto sem duplicidade e não sobrescrita.

## Limitações

- Seletores dependem da interface atual: leitura por `data-message-author-role`, `data-turn` ou `[class*=MarkdownRoot]`
  (a atual usa a terceira); botão de parar por `data-testid`/`aria-label`. Se mudarem, o script falha de forma segura (exit 4/6).
- O texto lido pode incluir sugestões de continuação que o ChatGPT acrescenta ao fim.
- Timeout/interrupção não clicam em "parar": a geração pode continuar no portal.
- Exige que o login no portal esteja ativo; o limite de uso é o da conta do usuário no ChatGPT.
- Sugestão (não aplicada): adicionar `consultas/` ao `.gitignore`, pois pode conter conteúdo sensível.

## Fase 2 — fluxo do coordenador (`consult-coordinator.mjs`)

Estado por pedido em `consultas/pedidos/<id>.json` (+ `.pergunta.md`); tudo ignorado pelo Git.

| Passo | Comando | Garantia |
|---|---|---|
| 1. Receber/avaliar | `prepare --from <Agente> --need "<por que só o ChatGPT resolve>" --tried "<o que já foi tentado>" --question "..."` (ou `--file`) | exige justificativa; bloqueia dados sensíveis (nada é registrado); avisa sobre pedidos anteriores iguais (nunca reaproveita) |
| 2. Aprovação humana | o coordenador mostra a **pergunta integral + hash** e pede ao humano que responda exatamente `APROVO <hash>` | a frase é ligada ao hash do texto exato |
| 3. Executar | `run <pedido> --human-confirmation "APROVO <hash>"` | recusa se a frase não bate, se a pergunta mudou (arquivo/JSON/hash) ou se o pedido já foi executado; marca `running` antes de enviar |
| 4. Verificar | automático: resposta não vazia, sem padrões sensíveis; avaliação de pertinência pelo coordenador | estado `answered` |
| 5. Encaminhar | `forward <pedido> --summary "<recomendação do coordenador>"` → `maestri ask "<solicitante>" ...` | só após `answered`; falha mantém a resposta; timeout = "incerto", sem reenvio sem `--force-forward` |
| 6. Registrar | `decide <pedido> --decision "..." --validation "..."` | anexa a `consultas/decisoes.md` |

Regra para o coordenador: **nunca** usar `--human-confirmation` sem que o humano tenha digitado a frase no chat
após ver a pergunta integral. Consulta sem pedido preparado e aprovado não existe.

## Interrupção após timeout

No timeout/SIGINT o script tenta parar a geração somente se (a) o `pathname` da página é exatamente o chat temporário
criado por aquela execução (`/c/<id>`, registrado após o envio) e (b) existe **um único** botão de parar
(`data-testid="stop-button"` ou `aria-label` exato Parar/Stop). Qualquer ambiguidade → não clica
(`meta.stop` = `path-mismatch|ambiguous|not-found|...`). Desative com `--no-stop`.
Além disso, o script **recusa navegar** um portal que esteja numa conversa/projeto fora de chat temporário.

## Extração da resposta

Antes de ler, botões, ícones e elementos cujo `data-testid`/classe contenha `suggest`/`follow` são ocultados
temporariamente (e restaurados) dentro do bloco da resposta, para excluir chips de sugestão.
