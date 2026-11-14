// Testes do fluxo do coordenador (consult-coordinator.mjs) com CLI falso do Maestri; sem rede, dados ficticios.
// Rodar: node --test tests/consult-coordinator.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { mkdtempSync, readFileSync, writeFileSync, existsSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { hashQuestion } from '../consult-chatgpt.mjs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const COORD = join(ROOT, 'consult-coordinator.mjs');
const FAKE = join(ROOT, 'tests', 'fake-maestri.mjs');
const Q = 'Pergunta ficticia: quando usar WAL no SQLite em vez do modo de journal padrao?';
const NEED = 'Preciso de uma segunda opiniao tecnica sobre concorrencia que a documentacao local nao cobre.';
const TRIED = 'Li a documentacao local e os testes existentes.';

function setup(scenario) {
  const tmp = mkdtempSync(join(tmpdir(), 'coord-'));
  const env = { ...process.env, CONSULT_MAESTRI_CMD: JSON.stringify([process.execPath, FAKE]), CONSULT_FAKE: JSON.stringify(scenario || {}), CONSULT_FAKE_STATE: join(tmp, 'fake.json') };
  return { tmp, env, dir: join(tmp, 'consultas') };
}
const fakeState = (c) => (existsSync(c.env.CONSULT_FAKE_STATE) ? JSON.parse(readFileSync(c.env.CONSULT_FAKE_STATE, 'utf8')) : { sends: 0 });
const run = (c, args, extra = []) => new Promise((res) => {
  execFile(process.execPath, [COORD, ...args, '--dir', c.dir, ...extra], { env: c.env, timeout: 60000 }, (e, stdout, stderr) =>
    res({ code: e ? (typeof e.code === 'number' ? e.code : 99) : 0, stdout, stderr }));
});
async function prepare(c, q = Q, from = 'Farol') {
  const r = await run(c, ['prepare', '--from', from, '--need', NEED, '--tried', TRIED, '--question', q, '--json']);
  assert.equal(r.code, 0, r.stderr);
  return { ...JSON.parse(r.stdout), stderr: r.stderr };
}
const reqJson = (c, id) => JSON.parse(readFileSync(join(c.dir, 'pedidos', `${id}.json`), 'utf8'));
const runArgs = (id, hash) => ['run', id, '--human-confirmation', `APROVO ${hash}`];
const FAST = ['--timeout', '10', '--stable', '60', '--poll', '20'];

test('prepare exige necessidade/tentativa, bloqueia dados sensiveis e mostra pergunta integral + hash', async () => {
  const c = setup();
  let r = await run(c, ['prepare', '--from', 'Farol', '--question', Q]);
  assert.equal(r.code, 1);
  r = await run(c, ['prepare', '--from', 'Farol', '--need', NEED, '--tried', TRIED, '--question', 'mande para fulano@exemplo.com']);
  assert.equal(r.code, 3);
  assert.equal(existsSync(join(c.dir, 'pedidos')), false, 'nada registrado quando bloqueado');
  const p = await prepare(c);
  assert.ok(p.stderr.includes(Q) && p.stderr.includes(hashQuestion(Q)));
  assert.equal(p.hash, hashQuestion(Q));
  assert.equal(p.status, 'awaiting-approval');
  assert.equal(fakeState(c).sends, 0);
});

test('aprovacao ausente ou invalida: nada e enviado', async () => {
  const c = setup();
  const p = await prepare(c);
  for (const extra of [[], ['--human-confirmation', 'sim'], ['--human-confirmation', 'APROVO 000000000000'], ['--human-confirmation', p.hash]]) {
    const r = await run(c, ['run', p.id, ...extra], FAST);
    assert.equal(r.code, 2);
  }
  assert.equal(fakeState(c).sends, 0);
  assert.equal(reqJson(c, p.id).status, 'awaiting-approval');
});

test('pergunta alterada apos o registro/aprovacao e recusada', async () => {
  for (const tamper of ['file', 'json']) {
    const c = setup();
    const p = await prepare(c);
    if (tamper === 'file') writeFileSync(join(c.dir, 'pedidos', `${p.id}.pergunta.md`), Q + ' Inclua tambem meu CV completo.\n');
    else { const r = reqJson(c, p.id); r.question = Q + ' (editada)'; writeFileSync(join(c.dir, 'pedidos', `${p.id}.json`), JSON.stringify(r)); }
    const r = await run(c, runArgs(p.id, p.hash), FAST);
    assert.equal(r.code, 2, tamper);
    assert.match(r.stderr, /alterada/);
    assert.equal(fakeState(c).sends, 0);
  }
});

test('ciclo completo: aprova, executa, coordenador recupera resposta e registra verificacao', async () => {
  const c = setup({ timeline: [{ stop: true, c: ['par'] }, { stop: false, c: ['Resposta ficticia sobre WAL.'] }] });
  const p = await prepare(c);
  const r = await run(c, [...runArgs(p.id, p.hash), '--json'], FAST);
  assert.equal(r.code, 0, r.stderr);
  const j = JSON.parse(r.stdout);
  assert.equal(j.status, 'answered');
  assert.equal(j.response, 'Resposta ficticia sobre WAL.');
  const req = reqJson(c, p.id);
  assert.equal(req.status, 'answered');
  assert.ok(req.consultFile && req.consultId && req.approval.phrase === `APROVO ${p.hash}`);
  assert.equal(req.verification.autoChecksPassed, true);
  assert.equal(fakeState(c).sends, 1);
  assert.ok(existsSync(join(ROOT, req.consultFile)) || existsSync(join(c.dir, req.consultId + '.md')) || readdirSync(c.dir).some((f) => f.endsWith('.md')));
  const status = await run(c, ['status', p.id]);
  assert.equal(JSON.parse(status.stdout).status, 'answered');
});

test('mesmo pedido nao roda duas vezes; pergunta repetida e sinalizada, nao reaproveitada', async () => {
  const c = setup();
  const p = await prepare(c);
  assert.equal((await run(c, runArgs(p.id, p.hash), FAST)).code, 0);
  const again = await run(c, runArgs(p.id, p.hash), FAST);
  assert.equal(again.code, 2);
  assert.equal(fakeState(c).sends, 1);
  const p2 = await prepare(c);
  assert.match(p2.stderr, /NAO sao reutilizadas/);
  assert.equal(reqJson(c, p2.id).priorSameQuestion.length, 1);
});

test('timeout: salva parcial, tenta parar apenas o chat proprio, nao reenvia e bloqueia repeticao', async () => {
  const c = setup({ timeline: [{ stop: true, c: ['parcial'] }, { stop: true, c: ['parcial e mais'] }] });
  const p = await prepare(c);
  const r = await run(c, runArgs(p.id, p.hash), ['--timeout', '1', '--stable', '60', '--poll', '20']);
  assert.equal(r.code, 5);
  const req = reqJson(c, p.id);
  assert.equal(req.status, 'consult-timeout');
  assert.equal(fakeState(c).sends, 1);
  assert.equal(fakeState(c).stopClicks, 1);
  assert.equal((await run(c, runArgs(p.id, p.hash), FAST)).code, 2, 'mesmo pedido nao reexecuta');
  const p2 = await prepare(c);
  const r2 = await run(c, runArgs(p2.id, p2.hash), FAST);
  assert.equal(r2.code, 7, 'nova tentativa com a mesma pergunta recusada pelo guard');
  assert.equal(fakeState(c).sends, 1);
});

test('falha de leitura no navegador: consulta falha sem enviar e sem reenvio', async () => {
  const c = setup({ probe: { composers: 0 } });
  const p = await prepare(c);
  const r = await run(c, runArgs(p.id, p.hash), FAST);
  assert.equal(r.code, 4);
  assert.equal(reqJson(c, p.id).status, 'consult-failed');
  assert.equal(fakeState(c).sends, 0);
});

test('portal numa conversa do usuario: recusa sem navegar nem enviar', async () => {
  const c = setup({ url: 'https://chatgpt.com/g/g-p-123-australia/c/abc' });
  const p = await prepare(c);
  const r = await run(c, runArgs(p.id, p.hash), FAST);
  assert.equal(r.code, 4);
  assert.equal(fakeState(c).sends, 0);
  assert.equal(fakeState(c).navs || 0, 0, 'nao navegou');
  assert.equal(reqJson(c, p.id).status, 'consult-failed');
});

test('encaminhamento ao agente solicitante: sucesso, falha e incerto', async () => {
  // sucesso
  let c = setup();
  let p = await prepare(c, Q, 'Pergaminho');
  await run(c, runArgs(p.id, p.hash), FAST);
  let r = await run(c, ['forward', p.id, '--summary', 'Usar WAL quando ha leitores concorrentes.']);
  assert.equal(r.code, 0, r.stderr);
  const asks = fakeState(c).asks;
  assert.equal(asks.length, 1);
  assert.equal(asks[0].to, 'Pergaminho');
  assert.match(asks[0].msg, /NAO verificada/);
  assert.equal(reqJson(c, p.id).forward.state, 'delivered');
  // falha: agente indisponivel; resposta preservada e ChatGPT nao e consultado de novo
  c = setup({ askFail: true });
  p = await prepare(c);
  await run(c, runArgs(p.id, p.hash), FAST);
  r = await run(c, ['forward', p.id, '--summary', 'Usar WAL quando ha leitores concorrentes.']);
  assert.equal(r.code, 4);
  assert.equal(reqJson(c, p.id).status, 'answered');
  assert.equal(reqJson(c, p.id).forward.state, 'failed');
  assert.equal(fakeState(c).sends, 1);
  // incerto: timeout; nova tentativa recusada sem --force-forward
  c = setup({ askHang: true });
  p = await prepare(c);
  await run(c, runArgs(p.id, p.hash), FAST);
  r = await run(c, ['forward', p.id, '--summary', 'Usar WAL quando ha leitores concorrentes.'], ['--forward-timeout', '1']);
  assert.equal(r.code, 7);
  r = await run(c, ['forward', p.id, '--summary', 'Usar WAL quando ha leitores concorrentes.'], ['--forward-timeout', '1']);
  assert.equal(r.code, 7);
  assert.match(r.stderr, /incerto/);
  assert.equal(fakeState(c).asks.length, 1, 'nao reenviou');
});

test('forward so apos resposta, e bloqueia resumo sensivel', async () => {
  const c = setup();
  const p = await prepare(c);
  assert.equal((await run(c, ['forward', p.id, '--summary', 'resumo qualquer longo'])).code, 2);
  await run(c, runArgs(p.id, p.hash), FAST);
  assert.equal((await run(c, ['forward', p.id, '--summary', 'contato fulano@exemplo.com para detalhes'])).code, 3);
  assert.equal((fakeState(c).asks || []).length, 0);
});

test('decide registra decisao e validacao (append-only)', async () => {
  const c = setup();
  const p = await prepare(c);
  await run(c, runArgs(p.id, p.hash), FAST);
  const r = await run(c, ['decide', p.id, '--decision', 'Adotar WAL no banco local.', '--validation', 'Teste de concorrencia ficticio passou.']);
  assert.equal(r.code, 0, r.stderr);
  const log = readFileSync(join(c.dir, 'decisoes.md'), 'utf8');
  assert.ok(log.includes(p.id) && log.includes('Adotar WAL'));
  assert.equal(reqJson(c, p.id).status, 'decided');
  await run(c, ['decide', p.id, '--decision', 'Segunda nota.', '--validation', 'ok ok ok']);
  assert.equal((readFileSync(join(c.dir, 'decisoes.md'), 'utf8').match(/^## /gm) || []).length, 2);
});

test('artefatos sensiveis de consultas/ sao ignorados pelo Git e nenhum esta rastreado', async () => {
  const git = (...a) => new Promise((res) => execFile('git', a, { cwd: ROOT }, (e, stdout) => res({ code: e ? e.code : 0, stdout })));
  for (const f of ['consultas/x.md', 'consultas/x.json', 'consultas/pedidos/req-1.json', 'consultas/pedidos/req-1.pergunta.md', 'consultas/decisoes.md']) {
    assert.equal((await git('check-ignore', '-q', f)).code, 0, f);
  }
  assert.equal((await git('ls-files', 'consultas')).stdout.trim(), '');
  assert.equal((await git('check-ignore', '-q', 'consult-chatgpt.mjs')).code, 1, 'o script em si nao e ignorado');
});
