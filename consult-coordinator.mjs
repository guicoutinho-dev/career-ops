#!/usr/bin/env node
/**
 * consult-coordinator.mjs -- fluxo controlado de consultas ao ChatGPT para o Antigravity_Mestre.
 *
 *  1. prepare  : registra o PEDIDO de um agente, valida (necessidade, dados sensiveis) e mostra a pergunta integral + hash.
 *  2. run      : so executa com a frase humana exata "APROVO <hash>"; chama consult-chatgpt.mjs com a aprovacao correspondente.
 *  3. forward  : encaminha o resumo do coordenador ao agente solicitante via `maestri ask` (nao reenvia ao ChatGPT).
 *  4. decide   : registra a decisao adotada e o resultado da validacao.
 *  5. status   : mostra o estado de um pedido.
 *
 * Exemplos:
 *   node consult-coordinator.mjs prepare --from Farol --need "..." --tried "..." --question "..."   (ou --file q.md)
 *   node consult-coordinator.mjs run <pedido> --human-confirmation "APROVO <hash>"
 *   node consult-coordinator.mjs forward <pedido> --summary "..."
 *   node consult-coordinator.mjs decide <pedido> --decision "..." --validation "..."
 *
 * Opcoes globais: --dir PASTA (padrao consultas), --portal NOME, --timeout S, --json
 * Pedidos ficam em <dir>/pedidos/<id>.json (+ .pergunta.md). Nada e reutilizado automaticamente.
 * Codigos de saida: 0 ok | 1 uso | 2 aprovacao ausente/invalida/estado invalido | 3 conteudo bloqueado
 *   4 falha da consulta | 5 timeout | 6 resposta nao verificada | 7 envio incerto/encaminhamento incerto
 */
import { execFile } from 'node:child_process';
import { createHash, randomBytes } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync, appendFileSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { forbiddenFileReason, hashQuestion, normalizeQuestion, runMaestri, scanForbidden } from './consult-chatgpt.mjs';

const ROOT = dirname(fileURLToPath(import.meta.url));
const CONSULT = join(ROOT, 'consult-chatgpt.mjs');
const err = (...a) => process.stderr.write(a.join(' ') + '\n');
const now = () => new Date().toISOString();
const stamp = () => now().replace(/[-:]/g, '').replace(/\..+/, '').replace('T', '-');

/** Frase que o HUMANO deve digitar no chat para aprovar exatamente esta pergunta. */
export const approvalPhrase = (hash) => `APROVO ${hash}`;

function parse(argv) {
  const o = { dir: 'consultas', timeout: 120, forwardTimeout: 120, pos: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const need = () => { if (i + 1 >= argv.length) throw new Error(`faltando valor para ${a}`); return argv[++i]; };
    const map = { '--from': 'from', '--need': 'need', '--tried': 'tried', '--question': 'question', '--file': 'file', '--dir': 'dir',
      '--portal': 'portal', '--human-confirmation': 'confirmation', '--summary': 'summary', '--decision': 'decision', '--validation': 'validation' };
    if (map[a]) o[map[a]] = need();
    else if (a === '--timeout') o.timeout = Number(need());
    else if (a === '--stable') o.stable = need();
    else if (a === '--poll') o.poll = need();
    else if (a === '--forward-timeout') o.forwardTimeout = Number(need());
    else if (a === '--json') o.json = true;
    else if (a === '--force-forward') o.forceForward = true;
    else if (a.startsWith('--')) throw new Error(`opcao desconhecida: ${a}`);
    else o.pos.push(a);
  }
  return o;
}

const paths = (dir) => ({ dir: resolve(ROOT, dir), ped: join(resolve(ROOT, dir), 'pedidos') });
const reqFile = (dir, id) => join(paths(dir).ped, `${id}.json`);
function load(dir, id) {
  if (!id || !/^req-[\w-]+$/.test(id)) throw new Error('identificador de pedido invalido');
  const f = reqFile(dir, id);
  if (!existsSync(f)) throw new Error(`pedido nao encontrado: ${id}`);
  return JSON.parse(readFileSync(f, 'utf8'));
}
function save(dir, r) { r.updatedAt = now(); writeFileSync(reqFile(dir, r.id), JSON.stringify(r, null, 2), 'utf8'); }
function allRequests(dir) {
  const p = paths(dir).ped;
  if (!existsSync(p)) return [];
  return readdirSync(p).filter((f) => f.endsWith('.json')).map((f) => { try { return JSON.parse(readFileSync(join(p, f), 'utf8')); } catch { return null; } }).filter(Boolean);
}
const sha = (s) => createHash('sha256').update(s, 'utf8').digest('hex');

// ---------------------------------------------------------------- prepare
function prepare(o) {
  if (!o.from || !/^[\w .-]{1,40}$/.test(o.from)) { err('Erro: --from NOME_DO_AGENTE obrigatorio.'); return 1; }
  if (!o.need || o.need.trim().length < 20) { err('Erro: --need (por que so o ChatGPT resolve; >= 20 caracteres) obrigatorio. Avalie antes se a consulta e necessaria.'); return 1; }
  if (!o.tried || o.tried.trim().length < 10) { err('Erro: --tried (o que ja foi tentado/consultado localmente) obrigatorio.'); return 1; }
  let q;
  if (o.file) {
    const why = forbiddenFileReason(o.file);
    if (why) { err(`BLOQUEADO: arquivo-fonte nao permitido (${why}).`); return 3; }
    if (!existsSync(o.file)) { err('Erro: arquivo nao encontrado.'); return 1; }
    q = normalizeQuestion(readFileSync(o.file, 'utf8'));
  } else if (o.question) q = normalizeQuestion(o.question);
  else { err('Erro: informe --question ou --file.'); return 1; }
  if (!q) { err('Erro: pergunta vazia.'); return 1; }

  // o texto de justificativa tambem sai do projeto? Nao: so a pergunta e enviada. Mesmo assim, nao armazenar segredos.
  const findings = [...scanForbidden(q), ...scanForbidden(`${o.need}\n${o.tried}`).map((f) => ({ ...f, categoria: `justificativa:${f.categoria}` }))];
  if (findings.length) {
    err('BLOQUEADO: dados possivelmente sensiveis (categoria: quantidade). Reescreva removendo-os; nada foi registrado:');
    for (const f of findings) err(`  - ${f.categoria}: ${f.quantidade}`);
    return 3;
  }

  const hash = hashQuestion(q);
  const prior = allRequests(o.dir).filter((r) => r.questionHash === hash);
  const open = prior.filter((r) => ['running', 'consult-send-uncertain'].includes(r.status));
  if (open.length) { err(`RECUSADO: pedido anterior com a mesma pergunta em estado incerto (${open.map((r) => r.id).join(', ')}). Verifique o portal; nao sera reenviado.`); return 7; }

  const { ped } = paths(o.dir);
  mkdirSync(ped, { recursive: true });
  const id = `req-${stamp()}-${hash.slice(0, 6)}${randomBytes(2).toString('hex')}`;
  const qFile = join(ped, `${id}.pergunta.md`);
  writeFileSync(qFile, q + '\n', { encoding: 'utf8', flag: 'wx' });
  const r = {
    id, requester: o.from, need: o.need.trim(), tried: o.tried.trim(), question: q, questionHash: hash, questionSha256: sha(q),
    questionFile: relative(ROOT, qFile), status: 'awaiting-approval', createdAt: now(),
    priorSameQuestion: prior.map((p) => ({ id: p.id, status: p.status, consultFile: p.consultFile || null })),
  };
  writeFileSync(reqFile(o.dir, id), JSON.stringify(r, null, 2), { encoding: 'utf8', flag: 'wx' });

  err('=== PERGUNTA INTEGRAL QUE SERA ENVIADA AO CHATGPT (servico externo) ===');
  err(q);
  err('=== FIM ===');
  err(`Pedido: ${id}   Solicitante: ${o.from}   Hash: ${hash}`);
  err(`Necessidade: ${r.need}`);
  err(`Ja tentado localmente: ${r.tried}`);
  if (prior.length) err(`Aviso: ${prior.length} pedido(s) anterior(es) com a mesma pergunta (${prior.map((p) => `${p.id}:${p.status}`).join(', ')}). Respostas antigas NAO sao reutilizadas automaticamente: avalie a pertinencia.`);
  err('A deteccao de dados sensiveis e heuristica: leia o texto acima.');
  err(`AGUARDANDO APROVACAO HUMANA. O humano deve responder exatamente: ${approvalPhrase(hash)}`);
  if (o.json) process.stdout.write(JSON.stringify({ id, hash, status: r.status, approvalPhrase: approvalPhrase(hash) }) + '\n');
  else process.stdout.write(`${id}\n`);
  return 0;
}

// ---------------------------------------------------------------- run
function runConsult(o, r) {
  const args = [CONSULT, '--file', resolve(ROOT, r.questionFile), '--approve', r.questionHash, '--json', '--dir', o.dir, '--timeout', String(o.timeout)];
  if (o.portal) args.push('--portal', o.portal);
  if (o.stable) args.push('--stable', o.stable);
  if (o.poll) args.push('--poll', o.poll);
  return new Promise((res) => {
    execFile(process.execPath, args, { env: process.env, timeout: (o.timeout + 90) * 1000, maxBuffer: 16 * 1024 * 1024 }, (e, stdout) => {
      let j = null;
      try { j = JSON.parse(String(stdout).trim().split('\n').pop()); } catch { /* sem json */ }
      res({ code: e ? (typeof e.code === 'number' ? e.code : 99) : 0, json: j, killed: !!(e && e.killed) });
    });
  });
}

async function run(o) {
  if (!process.env.ALLOW_DEPRECATED_GPT_CONSULT) {
    err('DESATIVADO: A integracao externa com ChatGPT foi desativada da orquestracao principal. As decisoes estrategicas sao tomadas diretamente pelo usuario, sem consultas externas.');
    return 1;
  }
  let r;
  try { r = load(o.dir, o.pos[1]); } catch (e) { err('Erro:', e.message); return 1; }
  if (r.status !== 'awaiting-approval') { err(`RECUSADO: o pedido esta em "${r.status}"; so pedidos aguardando aprovacao podem ser executados (sem reenvio automatico).`); return 2; }
  // integridade: pergunta armazenada x hash x arquivo
  let fileQ = '';
  try { fileQ = normalizeQuestion(readFileSync(resolve(ROOT, r.questionFile), 'utf8')); } catch { /* tratado abaixo */ }
  if (hashQuestion(r.question) !== r.questionHash || fileQ !== r.question || sha(r.question) !== r.questionSha256) {
    err('RECUSADO: a pergunta foi alterada depois do registro (hash/conteudo divergem). Prepare um novo pedido e obtenha nova aprovacao.');
    return 2;
  }
  if (!o.confirmation || o.confirmation.trim().toUpperCase() !== approvalPhrase(r.questionHash).toUpperCase()) {
    err(`RECUSADO: confirmacao humana ausente/invalida. O humano deve responder exatamente: ${approvalPhrase(r.questionHash)}`);
    return 2;
  }
  r.approval = { phrase: approvalPhrase(r.questionHash), at: now(), note: 'frase digitada pelo humano e repassada pelo coordenador' };
  r.status = 'running'; r.startedAt = now(); save(o.dir, r); // marcado ANTES de enviar: protege contra duplicidade

  const c = await runConsult(o, r);
  const j = c.json;
  if (j) { r.consultId = j.id; r.consultFile = j.file; r.consultState = j.state; }
  if (c.code === 0 && j && j.state === 'ok') {
    const resp = String(j.response || '');
    const notes = [];
    if (!resp.trim()) notes.push('resposta vazia');
    const sens = scanForbidden(resp);
    if (sens.length) notes.push(`resposta contem padroes sensiveis (${sens.map((f) => f.categoria).join(',')})`);
    r.verification = { autoChecksPassed: notes.length === 0, notes, chars: resp.length, at: now(), pending: 'avaliacao de pertinencia pelo coordenador' };
    r.status = 'answered';
    save(o.dir, r);
    err(`OK: ${r.consultFile}. Avalie a pertinencia antes de encaminhar.`);
    process.stdout.write(o.json ? JSON.stringify({ id: r.id, status: r.status, consultFile: r.consultFile, response: resp, verification: r.verification }) + '\n' : resp + '\n');
    return 0;
  }
  const map = { 5: ['consult-timeout', 5], 6: ['consult-unverified', 6], 7: ['consult-send-uncertain', 7], 130: ['consult-interrupted', 5] };
  const [st, code] = map[c.code] || (c.killed ? ['consult-send-uncertain', 7] : ['consult-failed', 4]);
  r.status = st; r.failure = { exitCode: c.code, state: j ? j.state : null }; save(o.dir, r);
  err(`Consulta nao concluida (${st}). NAO foi reenviada. ${j ? `Parcial/diagnostico: ${j.file}` : ''}`);
  if (j && j.response && st === 'consult-timeout') err('Ha resposta parcial salva (nao verificada).');
  return code;
}

// ---------------------------------------------------------------- forward / decide / status
async function forward(o) {
  let r;
  try { r = load(o.dir, o.pos[1]); } catch (e) { err('Erro:', e.message); return 1; }
  if (r.status !== 'answered' && r.status !== 'decided') { err(`RECUSADO: so e possivel encaminhar pedidos respondidos (estado: ${r.status}).`); return 2; }
  if (!o.summary || o.summary.trim().length < 10) { err('Erro: --summary (resumo/recomendacao do coordenador) obrigatorio.'); return 1; }
  if (r.forward && r.forward.state === 'uncertain' && !o.forceForward) { err('RECUSADO: encaminhamento anterior ficou incerto (timeout). Confira o terminal do agente; use --force-forward se tiver certeza.'); return 7; }
  const sens = scanForbidden(o.summary);
  if (sens.length) { err(`BLOQUEADO: o resumo contem padroes sensiveis (${sens.map((f) => f.categoria).join(', ')}).`); return 3; }
  const msg = `[Consulta ${r.id}] Recomendacao do coordenador (baseada em consulta ao ChatGPT, NAO verificada): ${o.summary.trim()} | Detalhes: ${r.consultFile}. Voce nao deve enviar nada externo; a decisao final e do Antigravity_Mestre.`;
  const res = await runMaestri(['ask', r.requester, msg], { timeoutMs: o.forwardTimeout * 1000 });
  r.forward = r.forward || { attempts: 0 };
  r.forward.attempts++; r.forward.at = now();
  if (res.ok) { r.forward.state = 'delivered'; save(o.dir, r); err(`Encaminhado a ${r.requester}.`); return 0; }
  if (res.timedOut) { r.forward.state = 'uncertain'; save(o.dir, r); err('Encaminhamento sem confirmacao (timeout); pode ter sido entregue.'); return 7; }
  r.forward.state = 'failed'; save(o.dir, r);
  err(`Falha ao encaminhar a ${r.requester} (agente inexistente/indisponivel). A resposta continua salva; tente novamente depois ou encaminhe manualmente (consulta ChatGPT NAO sera repetida).`);
  return 4;
}

function decide(o) {
  let r;
  try { r = load(o.dir, o.pos[1]); } catch (e) { err('Erro:', e.message); return 1; }
  if (!['answered', 'decided'].includes(r.status)) { err(`RECUSADO: pedido em "${r.status}".`); return 2; }
  if (!o.decision || !o.validation) { err('Erro: --decision e --validation obrigatorios.'); return 1; }
  for (const t of [o.decision, o.validation]) if (scanForbidden(t).length) { err('BLOQUEADO: texto contem padroes sensiveis.'); return 3; }
  r.decisions = r.decisions || [];
  r.decisions.push({ at: now(), decision: o.decision.trim(), validation: o.validation.trim() });
  r.status = 'decided'; save(o.dir, r);
  appendFileSync(join(paths(o.dir).dir, 'decisoes.md'), `\n## ${now()} — ${r.id} (solicitante: ${r.requester})\n- Decisao: ${o.decision.trim()}\n- Validacao: ${o.validation.trim()}\n- Consulta: ${r.consultFile}\n`, 'utf8');
  err(`Decisao registrada em ${r.id} e consultas/decisoes.md.`);
  return 0;
}

function status(o) {
  try { const r = load(o.dir, o.pos[1]); process.stdout.write(JSON.stringify({ ...r, question: undefined }, null, 2) + '\n'); return 0; } catch (e) { err('Erro:', e.message); return 1; }
}

export async function main(argv) {
  let o;
  try { o = parse(argv); } catch (e) { err('Erro:', e.message); return 1; }
  const cmd = o.pos[0];
  switch (cmd) {
    case 'prepare': return prepare(o);
    case 'run': return run(o);
    case 'forward': return forward(o);
    case 'decide': return decide(o);
    case 'status': return status(o);
    default: err(readFileSync(fileURLToPath(import.meta.url), 'utf8').split('*/')[0]); return 1;
  }
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main(process.argv.slice(2)).then((c) => process.exit(c));
}
