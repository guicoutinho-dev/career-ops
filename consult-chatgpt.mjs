#!/usr/bin/env node
/**
 * consult-chatgpt.mjs -- consultoria tecnica via ChatGPT ja autenticado no portal do Maestri.
 *
 * Sem API da OpenAI, sem Codex, sem servicos pagos, sem cookies/tokens: usa apenas
 * `maestri portal ...` (navegador embutido do Maestri) com a sessao que o usuario ja abriu.
 *
 * Uso:
 *   node consult-chatgpt.mjs "pergunta"                      -> mostra a pergunta + hash e sai (codigo 2)
 *   node consult-chatgpt.mjs "pergunta" --approve <hash>     -> envia (hash = aprovacao DESTA pergunta)
 *   node consult-chatgpt.mjs --file pergunta.md [--approve <hash>]
 *
 * Opcoes: --portal NOME (padrao ChatGPT_Consultas) --timeout SEGUNDOS (padrao 120)
 *         --stable MS (padrao 3000) --poll MS (padrao 1000) --dir PASTA (padrao consultas)
 *         --dry-run (so valida) --json (saida JSON) --resend (permite reenviar apos envio incerto) --no-stop (nao interrompe a geracao no timeout)
 *
 * stdout: SOMENTE o texto da resposta (ou JSON com --json). Mensagens vao para stderr.
 * Codigos de saida: 0 ok | 1 uso | 2 aprovacao exigida/invalida | 3 conteudo bloqueado
 *   4 portal/seletor/login/captcha (usar modo manual) | 5 timeout | 6 resposta vazia/incompleta/ambigua
 *   7 envio incerto (nao reenviado) | 130 interrompido
 */
import { execFile } from 'node:child_process';
import { createHash, randomBytes } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, writeFileSync, readdirSync } from 'node:fs';
import { basename, dirname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import readline from 'node:readline';

const ROOT = dirname(fileURLToPath(import.meta.url));
const CHAT_URL = 'https://chatgpt.com/?temporary-chat=true';
const MAX_QUESTION_CHARS = 8000;

// ---------------------------------------------------------------- protecao de dados

/** Arquivos que NUNCA podem ser usados como fonte da pergunta (relativos a raiz, minusculos). */
export const FORBIDDEN_FILES = [
  'cv.md', 'config/profile.yml', 'config/profile.example.yml', 'config/cv-facts.json', 'modes/_profile.md',
  'modes/_custom.md', 'article-digest.md', 'voice-dna.md', 'portals.yml', '.env', 'cv-facts.json',
];
export const FORBIDDEN_DIRS = ['data', 'output', 'reports', 'interview-prep', 'writing-samples', 'jds', '.maestri', '.git', 'node_modules'];
const FORBIDDEN_NAME_RE = /(cookie|token|secret|credential|passw|\.pem$|\.key$|\.env)/i;

/** Campos de perfil cujo valor nao pode aparecer na pergunta. */
export const FORBIDDEN_PROFILE_FIELDS = ['full_name', 'name', 'email', 'phone', 'linkedin', 'github', 'location', 'address'];

const PATTERNS = [
  ['email', /[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+/],
  ['telefone', /(\+\d{1,3}[\s.-]?\(?\d{1,4}\)?([\s.-]?\d{2,4}){2,4}|\(\d{2,4}\)[\s.-]?\d{3,5}[\s.-]?\d{3,5}|\b04\d{2}[\s.-]?\d{3}[\s.-]?\d{3}\b)/],
  ['chave-api', /\b(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16})\b/],
  ['jwt', /\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b/],
  ['bearer', /\bbearer\s+[A-Za-z0-9._~+/=-]{12,}/i],
  ['chave-privada', /-----BEGIN [A-Z ]*PRIVATE KEY-----/],
  ['cookie', /\b(set-)?cookie\s*[:=]/i],
  ['senha', /\b(passw(or)?d|senha|secret|api[_-]?key|access[_-]?token|refresh[_-]?token)\s*[:=]\s*\S+/i],
  ['segredo-longo', /\b[A-Fa-f0-9]{40,}\b|\b[A-Za-z0-9+/_-]{48,}={0,2}(?=\s|$)/],
  ['caminho-pessoal', /[A-Za-z]:\\Users\\[^\\\s]+|\/home\/[^/\s]+|\/Users\/[^/\s]+/],
];

export function normalizeQuestion(q) {
  return String(q).replace(/\r\n?/g, '\n').trim();
}
export function hashQuestion(q) {
  return createHash('sha256').update(normalizeQuestion(q), 'utf8').digest('hex').slice(0, 12);
}
const squash = (s) => String(s).replace(/\s+/g, ' ').trim().toLowerCase();

/** Verifica se o caminho de um arquivo-fonte e proibido. Retorna motivo ou null. */
export function forbiddenFileReason(filePath, root = ROOT) {
  const abs = resolve(filePath);
  const rel = relative(root, abs).split(sep).join('/').toLowerCase();
  const name = basename(abs).toLowerCase();
  if (FORBIDDEN_FILES.includes(rel) || FORBIDDEN_FILES.includes(name)) return 'arquivo proibido';
  if (FORBIDDEN_NAME_RE.test(name)) return 'nome sugere credencial';
  if (!rel.startsWith('..') && FORBIDDEN_DIRS.some((d) => rel === d || rel.startsWith(d + '/'))) return 'diretorio proibido';
  if (!/\.(md|txt)$/i.test(name)) return 'somente .md ou .txt';
  return null;
}

function profileValues(root) {
  const vals = [];
  const p = join(root, 'config', 'profile.yml');
  if (!existsSync(p)) return vals;
  for (const line of readFileSync(p, 'utf8').split(/\r?\n/)) {
    const m = line.match(/^\s*([A-Za-z_]+)\s*:\s*["']?(.+?)["']?\s*$/);
    if (m && FORBIDDEN_PROFILE_FIELDS.includes(m[1].toLowerCase()) && m[2].length >= 4 && !m[2].includes('#')) vals.push(squash(m[2]));
  }
  return vals;
}
function sensitiveLines(root) {
  const out = [];
  for (const f of ['cv.md', 'article-digest.md']) {
    const p = join(root, f);
    if (!existsSync(p)) continue;
    for (const line of readFileSync(p, 'utf8').split(/\r?\n/)) {
      const s = squash(line);
      if (s.length >= 40) out.push(s);
    }
  }
  return out;
}

/**
 * Retorna lista de achados [{categoria, quantidade}] -- NUNCA o conteudo encontrado.
 * `root` permite testes com raiz ficticia.
 */
export function scanForbidden(question, root = ROOT) {
  const findings = [];
  const add = (cat, n = 1) => { const f = findings.find((x) => x.categoria === cat); if (f) f.quantidade += n; else findings.push({ categoria: cat, quantidade: n }); };
  for (const [cat, re] of PATTERNS) {
    const g = new RegExp(re.source, re.flags.includes('g') ? re.flags : re.flags + 'g');
    const m = question.match(g);
    if (m) add(cat, m.length);
  }
  const sq = squash(question);
  for (const v of profileValues(root)) if (sq.includes(v)) add('campo-do-perfil');
  for (const l of sensitiveLines(root)) if (sq.includes(l)) add('trecho-do-cv');
  return findings;
}

// ---------------------------------------------------------------- Maestri

function maestriCmd() {
  if (process.env.CONSULT_MAESTRI_CMD) return JSON.parse(process.env.CONSULT_MAESTRI_CMD);
  return [process.env.MAESTRI_CLI || 'maestri'];
}
export function runMaestri(args, { timeoutMs = 20000 } = {}) {
  const [bin, ...pre] = maestriCmd();
  return new Promise((res) => {
    execFile(bin, [...pre, ...args], { timeout: timeoutMs, windowsHide: true, maxBuffer: 8 * 1024 * 1024 }, (err, stdout, stderr) => {
      res({ ok: !err, stdout: String(stdout || ''), stderr: String(stderr || ''), timedOut: !!(err && err.killed) });
    });
  });
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class PortalError extends Error {
  constructor(code, msg) { super(msg); this.code = code; }
}

async function evalJson(portal, script) {
  const r = await runMaestri(['portal', 'evaluate', portal, script]);
  if (!r.ok) throw new PortalError('portal-error', r.timedOut ? 'maestri portal evaluate: timeout' : 'maestri portal evaluate falhou');
  const out = r.stdout.trim();
  try { return JSON.parse(out); } catch { throw new PortalError('eval-parse', 'resposta do navegador nao e JSON valido'); }
}

// Scripts executados DENTRO da pagina. Cada um tem um marcador /*op:...*/ (usado pelo CLI falso nos testes).
const VISIBLE = `const vis=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0};`;
const COMPOSER_SEL = `'#prompt-textarea,textarea,[contenteditable="true"],[role="textbox"]'`;

const PROBE_JS = `/*op:probe*/(()=>{${VISIBLE}
const title=document.title||'';
const challenge=/just a moment|um momento|attention required/i.test(title)||!!document.querySelector('iframe[src*="challenges"],#challenge-running,#challenge-form,[id*="turnstile"],[class*="cf-turnstile"]')||/verify you are human|verifique que voc[eê] [eé] humano/i.test((document.body&&document.body.innerText||'').slice(0,2000));
const comps=[...document.querySelectorAll(${COMPOSER_SEL})].filter(vis);
const login=[...document.querySelectorAll('button,a')].some(b=>/^(entrar|log in|sign in|cadastre-se gr[aá]tis|sign up)/i.test((b.innerText||'').trim()));
return JSON.stringify({path:location.pathname,temporary:/temporary-chat=true/.test(location.search),composers:comps.length,loginPrompt:login,challenge});})()`;

/** Elementos de interface (botoes, icones, chips de sugestao) ocultados so durante a leitura do texto da resposta. */
const HIDE_SEL = 'button,[role="button"],svg,[data-testid*="suggest" i],[data-testid*="follow" i],[class*="suggest" i],[class*="follow" i]';

const STATE_JS = `/*op:state*/(()=>{${VISIBLE}
const HIDE=${JSON.stringify(HIDE_SEL)};
const clean=(el)=>{const hs=[...el.querySelectorAll(HIDE)];const old=hs.map(h=>h.style.display);hs.forEach(h=>h.style.setProperty('display','none','important'));const t=el.innerText||'';hs.forEach((h,i)=>{h.style.display=old[i];});return t;};
const last=(sel)=>[...document.querySelectorAll(sel)].map(clean);
const comps=[...document.querySelectorAll(${COMPOSER_SEL})].filter(vis);
const c=comps[0];
return JSON.stringify({
 stop:!!document.querySelector('[data-testid="stop-button"],button[aria-label*="Parar"],button[aria-label*="Stop"]'),
 path:location.pathname,
 a:last('[data-message-author-role="assistant"]'),
 b:last('[data-turn="assistant"]'),
 c:last('[class*="MarkdownRoot"]'),
 uA:document.querySelectorAll('[data-message-author-role="user"]').length,
 uB:document.querySelectorAll('[data-turn="user"]').length,
 composerLen:c?((c.value!==undefined?c.value:c.innerText)||'').trim().length:-1});})()`;

const fillJs = (text) => `/*op:fill*/(()=>{${VISIBLE}
const t=${JSON.stringify(text)};const n=s=>String(s).replace(/\\s+/g,' ').trim();
const comps=[...document.querySelectorAll(${COMPOSER_SEL})].filter(vis);
if(comps.length!==1)return JSON.stringify({ok:false,reason:'composer-count',n:comps.length});
const el=comps[0];el.focus();
if(el.tagName==='TEXTAREA'){const set=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;set.call(el,t);el.dispatchEvent(new Event('input',{bubbles:true}));}
else{document.execCommand('selectAll',false,null);document.execCommand('insertText',false,t);}
const cur=el.value!==undefined?el.value:el.innerText;
return JSON.stringify({ok:true,match:n(cur)===n(t),len:n(cur).length});})()`;

const SEND_JS = `/*op:send*/(()=>{
const btns=[...document.querySelectorAll('[data-testid="send-button"],button[aria-label]')].filter(b=>b.matches('[data-testid="send-button"]')||/^(enviar|send)/i.test(b.getAttribute('aria-label')||''));
if(btns.length!==1)return JSON.stringify({ok:false,reason:'send-button-count',n:btns.length});
if(btns[0].disabled)return JSON.stringify({ok:false,reason:'send-button-disabled'});
btns[0].click();return JSON.stringify({ok:true});})()`;

/**
 * Interrompe a geracao SOMENTE se: (1) a pagina e exatamente o chat temporario criado por esta execucao
 * (pathname igual ao registrado) e (2) existe exatamente um botao de parar identificado por data-testid
 * ou por aria-label exato. Qualquer ambiguidade -> nao clica.
 */
const stopJs = (expectedPath) => `/*op:stop*/(()=>{
if(location.pathname!==${JSON.stringify(expectedPath)}||!/temporary-chat=true/.test(location.search))return JSON.stringify({ok:false,reason:'path-mismatch'});
const all=[...document.querySelectorAll('[data-testid="stop-button"],button[aria-label]')].filter(b=>b.matches('[data-testid="stop-button"]')||/^(parar( gera[cç][aã]o)?|stop( streaming| generating)?)$/i.test((b.getAttribute('aria-label')||'').trim()));
if(all.length===0)return JSON.stringify({ok:false,reason:'not-found'});
if(all.length>1)return JSON.stringify({ok:false,reason:'ambiguous'});
all[0].click();return JSON.stringify({ok:true});})()`;

async function tryStop(portal, chatPath) {
  if (!chatPath) return 'no-chat-path';
  try {
    const r = await evalJson(portal, stopJs(chatPath));
    if (!r.ok) return r.reason;
    for (let i = 0; i < 6; i++) { // confirma que parou
      await sleep(500);
      const s = await evalJson(portal, STATE_JS);
      if (!s.stop) return 'stopped';
    }
    return 'stop-unconfirmed';
  } catch { return 'error'; }
}

function pickAnswer(s) {
  for (const k of ['a', 'b', 'c']) {
    if (Array.isArray(s[k]) && s[k].length) {
      return { strategy: k, count: s[k].length, text: String(s[k][s[k].length - 1]).trim(), ambiguous: s[k].length > 1 };
    }
  }
  return { strategy: null, count: 0, text: '', ambiguous: false };
}
const looksIncomplete = (t) => ((t.match(/```/g) || []).length % 2) === 1;

// ---------------------------------------------------------------- persistencia

const stamp = () => new Date().toISOString().replace(/[-:]/g, '').replace(/\..+/, '').replace('T', '-');

function writeNew(path, content) {
  writeFileSync(path, content, { encoding: 'utf8', flag: 'wx' }); // nunca sobrescreve
}

/** Consultas anteriores com o mesmo hash cujo envio ficou incerto. */
function uncertainPrevious(dir, qhash) {
  if (!existsSync(dir)) return [];
  const bad = new Set(['submitting', 'send-unconfirmed', 'timeout', 'interrupted', 'incomplete', 'ambiguous', 'empty']);
  const out = [];
  for (const f of readdirSync(dir)) {
    if (!f.endsWith('.json')) continue;
    try {
      const m = JSON.parse(readFileSync(join(dir, f), 'utf8'));
      if (m.questionHash === qhash && bad.has(m.state)) out.push(m.id);
    } catch { /* ignora */ }
  }
  return out;
}

function renderMd(meta, question, response) {
  const label = meta.state === 'ok' ? 'Resposta' : `Resposta (${meta.state}, NAO verificada/parcial)`;
  return [
    `# Consulta ${meta.id}`, '',
    `- Estado: ${meta.state}`, `- Criada: ${meta.createdAt}`, `- Concluida: ${meta.finishedAt}`,
    `- Portal: ${meta.portal} (chat temporario)`, `- Hash da pergunta (aprovacao): ${meta.questionHash}`,
    `- Aprovacao: ${meta.approvedVia}`, `- Estrategia de leitura: ${meta.strategy ?? 'nenhuma'}`, '',
    '## Pergunta', '', question, '', `## ${label}`, '', response || '(vazia)', '',
  ].join('\n');
}

// ---------------------------------------------------------------- execucao principal

function parseArgs(argv) {
  const o = { timeout: 120, stable: 3000, poll: 1000, portal: 'ChatGPT_Consultas', dir: 'consultas', pos: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const need = () => { if (i + 1 >= argv.length) throw new Error(`faltando valor para ${a}`); return argv[++i]; };
    if (a === '--file') o.file = need();
    else if (a === '--approve') o.approve = need();
    else if (a === '--portal') o.portal = need();
    else if (a === '--dir') o.dir = need();
    else if (a === '--timeout') o.timeout = Number(need());
    else if (a === '--stable') o.stable = Number(need());
    else if (a === '--poll') o.poll = Number(need());
    else if (a === '--dry-run') o.dryRun = true;
    else if (a === '--json') o.json = true;
    else if (a === '--resend') o.resend = true;
    else if (a === '--no-stop') o.noStop = true;
    else if (a === '--help' || a === '-h') o.help = true;
    else if (a.startsWith('--')) throw new Error(`opcao desconhecida: ${a}`);
    else o.pos.push(a);
  }
  for (const k of ['timeout', 'stable', 'poll']) if (!Number.isFinite(o[k]) || o[k] <= 0) throw new Error(`--${k} invalido`);
  return o;
}

const err = (...a) => process.stderr.write(a.join(' ') + '\n');

async function askTty(hash) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stderr });
  const ans = await new Promise((r) => rl.question(`Para aprovar o envio desta pergunta digite o hash (${hash}): `, r));
  rl.close();
  return ans.trim();
}

export async function main(argv, { interruptedRef = { v: false } } = {}) {
  let opts;
  try { opts = parseArgs(argv); } catch (e) { err('Erro:', e.message); return 1; }
  if (opts.help) { err(readFileSync(fileURLToPath(import.meta.url), 'utf8').split('*/')[0]); return 0; }

  // 1. pergunta
  let question;
  if (opts.file) {
    const why = forbiddenFileReason(opts.file);
    if (why) { err(`BLOQUEADO: arquivo-fonte nao permitido (${why}).`); return 3; }
    if (!existsSync(opts.file)) { err('Erro: arquivo nao encontrado.'); return 1; }
    question = normalizeQuestion(readFileSync(opts.file, 'utf8'));
  } else if (opts.pos.length) {
    question = normalizeQuestion(opts.pos.join(' '));
  } else { err('Erro: informe a pergunta como argumento ou use --file. (--help)'); return 1; }
  if (!question) { err('Erro: pergunta vazia.'); return 1; }
  if (question.length > MAX_QUESTION_CHARS) { err(`Erro: pergunta maior que ${MAX_QUESTION_CHARS} caracteres.`); return 1; }

  // 2. protecao de dados
  const findings = scanForbidden(question);
  if (findings.length) {
    err('BLOQUEADO: a pergunta contem dados possivelmente sensiveis (categoria: quantidade) -- nada foi enviado:');
    for (const f of findings) err(`  - ${f.categoria}: ${f.quantidade}`);
    return 3;
  }

  // 3. aprovacao especifica desta pergunta
  const qhash = hashQuestion(question);
  err('=== CONTEUDO QUE SERA ENVIADO AO CHATGPT (externo) ===');
  err(question);
  err('=== FIM ===');
  err(`Hash de aprovacao desta pergunta: ${qhash}`);
  err('Aviso: a deteccao automatica nao garante a ausencia de dados pessoais; revise o texto acima.');
  let approvedVia = null;
  if (opts.approve) {
    if (opts.approve.trim() !== qhash) { err('RECUSADO: o hash de aprovacao nao corresponde a esta pergunta (o conteudo mudou?). Nada foi enviado.'); return 2; }
    approvedVia = 'flag --approve';
  } else if (!opts.dryRun && process.stdin.isTTY) {
    if ((await askTty(qhash)) !== qhash) { err('RECUSADO: aprovacao nao concedida. Nada foi enviado.'); return 2; }
    approvedVia = 'confirmacao interativa';
  } else if (!opts.dryRun) {
    err(`APROVACAO EXIGIDA. Se um humano aprovou, rode novamente com: --approve ${qhash}`);
    return 2;
  }
  if (opts.dryRun) { err('Dry-run: validacoes OK, nada enviado.'); return 0; }

  // 4. guarda anti-duplicidade entre execucoes
  const dir = resolve(ROOT, opts.dir);
  const prev = uncertainPrevious(dir, qhash);
  if (prev.length && !opts.resend) {
    err(`RECUSADO: ja existe consulta com esta pergunta cujo resultado ficou incerto (${prev.join(', ')}). Confira o portal; use --resend somente se tiver certeza de que nao foi enviada.`);
    return 7;
  }

  mkdirSync(dir, { recursive: true });
  const id = `${stamp()}-${qhash.slice(0, 6)}${randomBytes(2).toString('hex')}`;
  const meta = {
    id, questionHash: qhash, questionChars: question.length, portal: opts.portal, approvedVia,
    createdAt: new Date().toISOString(), state: 'approved', sent: false, strategy: null, history: [],
  };
  const metaPath = join(dir, `${id}.json`);
  const mdPath = join(dir, `${id}.md`);
  const setState = (s) => { meta.state = s; meta.history.push({ s, t: new Date().toISOString() }); writeFileSync(metaPath, JSON.stringify(meta, null, 2), { encoding: 'utf8', flag: meta.history.length === 1 ? 'wx' : 'w' }); };
  setState('approved');

  let response = '';
  let exit = 0;
  const finish = (state, code) => { meta.finishedAt = new Date().toISOString(); meta.exitCode = code; meta.responseChars = response.length; setState(state); writeNew(mdPath, renderMd(meta, question, response)); };
  const manualHint = () => err(`Modo manual: cole a pergunta de ${relative(ROOT, mdPath)} no ChatGPT e salve a resposta em ${relative(ROOT, join(dir, id + '.resposta-manual.md'))}.`);
  const out = () => {
    if (opts.json) process.stdout.write(JSON.stringify({ id, state: meta.state, file: relative(ROOT, mdPath), response }) + '\n');
    else if (meta.state === 'ok') process.stdout.write(response + '\n');
  };

  try {
    // 5. portal pronto?
    // 5. portal pronto? Nunca navegar um portal que esta numa conversa/projeto do usuario.
    const info = await runMaestri(['portal', 'info', opts.portal]);
    if (!info.ok) throw new PortalError('portal-error', `portal "${opts.portal}" indisponivel (maestri list)`);
    const curUrl = (info.stdout.match(/^url:\s*(\S+)/m) || [])[1] || '';
    if (/chatgpt\.com\/(c|g)\//i.test(curUrl) && !/temporary-chat=true/.test(curUrl)) {
      throw new PortalError('portal-in-use', 'o portal esta numa conversa/projeto fora de um chat temporario; use um portal dedicado a consultas (--portal) -- nada foi alterado');
    }
    const nav = await runMaestri(['portal', 'navigate', opts.portal, CHAT_URL]);
    if (!nav.ok) throw new PortalError('portal-error', `portal "${opts.portal}" indisponivel (maestri list)`);
    let probe = null;
    for (let i = 0; i < 20; i++) {
      probe = await evalJson(opts.portal, PROBE_JS);
      if (probe.composers >= 1 || probe.challenge || probe.loginPrompt) break;
      await sleep(Math.min(opts.poll, 1000));
    }
    if (probe.challenge) throw new PortalError('challenge', 'verificacao de seguranca/CAPTCHA detectada -- resolva manualmente no portal; nao sera contornada');
    if (probe.loginPrompt) throw new PortalError('login', 'portal sem sessao autenticada -- faca login manualmente no portal');
    if (probe.composers !== 1) throw new PortalError('selector', `campo de entrada ambiguo/ausente (encontrados: ${probe.composers})`);
    if (!probe.temporary || /^\/c\//.test(probe.path)) throw new PortalError('not-fresh', 'a pagina nao e um chat temporario novo');
    const base = await evalJson(opts.portal, STATE_JS);
    if (base.uA || base.uB || (base.a && base.a.length) || (base.b && base.b.length)) throw new PortalError('not-fresh', 'o chat novo ja contem mensagens');

    // 6. preencher e validar ANTES de enviar
    const fill = await evalJson(opts.portal, fillJs(question));
    if (!fill.ok) throw new PortalError('selector', `falha ao preencher o campo (${fill.reason})`);
    if (!fill.match) throw new PortalError('fill-mismatch', 'o texto no campo difere da pergunta aprovada -- nada enviado');

    // 7. envio unico (nunca repetido)
    meta.sent = true; setState('submitting');
    let sendRes;
    try { sendRes = await evalJson(opts.portal, SEND_JS); } catch { sendRes = null; }
    if (!sendRes || !sendRes.ok) {
      meta.sent = !!(sendRes === null);
      response = '';
      if (sendRes === null) { finish('send-unconfirmed', 7); err('Envio incerto: o clique pode ter ocorrido. NAO foi reenviado.'); manualHint(); out(); return 7; }
      meta.sent = false; // botao nao encontrado/desabilitado: nada foi enviado
      throw new PortalError('selector', `botao de envio indisponivel (${sendRes.reason})`);
    }
    let confirmed = false; let chatPath = null; let sawStop = false; let stopOffSince = Date.now();
    for (let i = 0; i < 15 && !confirmed; i++) {
      await sleep(Math.min(opts.poll, 1000));
      const s = await evalJson(opts.portal, STATE_JS);
      if (s.stop) { sawStop = true; stopOffSince = Date.now(); }
      if (!chatPath && /^\/c\/[\w-]+$/.test(s.path || '')) chatPath = s.path;
      confirmed = (s.uA > 0 || s.uB > 0 || s.stop || pickAnswer(s).text.length > 0) && s.composerLen === 0;
    }
    if (!confirmed) { finish('send-unconfirmed', 7); err('Envio nao confirmado. NAO foi reenviado.'); manualHint(); out(); return 7; }

    // 8. esperar o fim da geracao
    const halt = async () => {
      if (opts.noStop) return (meta.stop = 'disabled');
      meta.stop = await tryStop(opts.portal, chatPath);
      if (meta.stop === 'stopped') { try { const p = pickAnswer(await evalJson(opts.portal, STATE_JS)); if (p.text) pick = p; } catch { /* mantem parcial */ } }
      return meta.stop;
    };
    const t0 = Date.now();
    let last = null; let lastChange = Date.now(); let pick = { text: '' };
    for (;;) {
      if (interruptedRef.v) { const st = await halt(); response = pick.text; finish('interrupted', 130); err(`Interrompido. Parada da geracao: ${st}. Resposta parcial salva.`); out(); return 130; }
      if (Date.now() - t0 > opts.timeout * 1000) { const st = await halt(); response = pick.text; finish('timeout', 5); err(`Timeout de ${opts.timeout}s. Parada da geracao: ${st}. Resposta parcial salva. NAO foi reenviado.`); out(); return 5; }
      const s = await evalJson(opts.portal, STATE_JS);
      pick = pickAnswer(s);
      sawStop = sawStop || s.stop;
      if (!chatPath && /^\/c\/[\w-]+$/.test(s.path || '')) chatPath = s.path;
      if (s.stop) stopOffSince = Date.now();
      if (pick.text !== last) { last = pick.text; lastChange = Date.now(); }
      const now = Date.now();
      // fim = botao de parar ausente + texto estavel; texto vazio so conta se a geracao foi vista e terminou
      if (!s.stop && now - lastChange >= opts.stable && (pick.text.length > 0 || (sawStop && now - stopOffSince >= opts.stable))) break;
      await sleep(opts.poll);
    }

    // 9. validar resposta
    meta.strategy = pick.strategy; meta.blocks = pick.count; meta.sawStopButton = sawStop;
    response = pick.text;
    if (!response) { finish('empty', 6); err('Resposta vazia.'); manualHint(); out(); return 6; }
    if (pick.ambiguous) { finish('ambiguous', 6); err(`Leitura ambigua (${pick.count} blocos de resposta num chat novo). Texto salvo como nao verificado.`); manualHint(); out(); return 6; }
    if (looksIncomplete(response)) { finish('incomplete', 6); err('Resposta possivelmente incompleta (bloco de codigo aberto).'); manualHint(); out(); return 6; }
    finish('ok', 0); err(`OK: ${relative(ROOT, mdPath)}`); out();
  } catch (e) {
    const code = e instanceof PortalError ? e.code : 'internal';
    meta.diagnostic = { code, message: e instanceof PortalError ? e.message : 'erro interno inesperado' }; // sem conteudo sensivel
    exit = 4;
    if (meta.sent) exit = 7;
    finish(meta.sent ? 'send-unconfirmed' : `failed-${code}`, exit);
    err(`FALHA (${code}): ${meta.diagnostic.message}. ${meta.sent ? 'Envio pode ter ocorrido; NAO foi reenviado.' : 'Nada foi enviado.'}`);
    manualHint(); out();
    return exit;
  }
  return exit;
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  const ref = { v: false };
  process.on('SIGINT', () => { ref.v = true; });
  process.on('SIGTERM', () => { ref.v = true; });
  main(process.argv.slice(2), { interruptedRef: ref }).then((c) => process.exit(c));
}
