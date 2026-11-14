#!/usr/bin/env node
// CLI falso de `maestri portal ...` para testar consult-chatgpt.mjs sem tocar no ChatGPT real.
// Cenario em CONSULT_FAKE (JSON); estado/contadores em CONSULT_FAKE_STATE (arquivo JSON).
import { existsSync, readFileSync, writeFileSync } from 'node:fs';

const sc = JSON.parse(process.env.CONSULT_FAKE || '{}');
const stPath = process.env.CONSULT_FAKE_STATE;
const st = existsSync(stPath) ? JSON.parse(readFileSync(stPath, 'utf8')) : { sends: 0, polls: 0, sent: false, navs: 0 };
const save = () => writeFileSync(stPath, JSON.stringify(st));
const [, , portal, verb, , script = ''] = process.argv; // argv: portal <verb> <name> [arg]
const op = (script.match(/\/\*op:(\w+)\*\//) || [])[1];

if (portal === 'ask') { // maestri ask "Agente" "mensagem"
  st.asks = st.asks || []; st.asks.push({ to: process.argv[3], msg: process.argv[4] }); save();
  if (sc.askFail) { console.error('maestri: error: unknown agent'); process.exit(1); }
  if (sc.askHang) { await new Promise((r) => setTimeout(r, 60000)); process.exit(0); } else { console.log('ok'); process.exit(0); }
}
if (portal !== 'portal') { process.exit(1); }
if (verb === 'info') { console.log('url: ' + (sc.url || 'https://chatgpt.com/?temporary-chat=true')); process.exit(0); }
if (verb === 'navigate') {
  if (sc.navFail) { console.error('maestri: error: no such portal'); process.exit(1); }
  st.navs++; save(); console.log('ok'); process.exit(0);
}
if (verb !== 'evaluate') process.exit(1);

const print = (o) => { console.log(JSON.stringify(o)); process.exit(0); };
if (op === 'probe') print({ path: '/', temporary: true, composers: 1, loginPrompt: false, challenge: false, ...(sc.probe || {}) });
if (op === 'fill') print({ ok: true, match: true, len: 5, ...(sc.fill || {}) });
if (op === 'send') {
  if (sc.sendCrash) { st.sends++; st.sent = true; save(); console.error('maestri: error: connection lost'); process.exit(1); }
  const r = { ok: true, ...(sc.send || {}) };
  if (r.ok) { st.sends++; st.sent = true; save(); }
  print(r);
}
if (op === 'stop') { const r = { ok: true, ...(sc.stop || {}) }; if (r.ok) { st.stopped = true; st.stopClicks = (st.stopClicks || 0) + 1; save(); } print(r); }
if (op === 'state') {
  if (!st.sent) print({ stop: false, a: [], b: [], c: [], uA: 0, uB: 0, composerLen: 5 });
  const tl = sc.timeline || [{ stop: false, c: ['Resposta curta de teste.'] }];
  const frame = tl[Math.min(st.polls, tl.length - 1)];
  st.polls++; save();
  print({ stop: false, path: '/c/abc-123', a: [], b: [], c: [], uA: 1, uB: 0, composerLen: 0, ...frame, ...(st.stopped ? { stop: false } : {}) });
}
console.error('op desconhecida');
process.exit(1);
