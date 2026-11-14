import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SHARED_PROVIDERS = path.resolve(HERE, '../../../providers');

const { loadProviders, resolveProvider } = await import(
  pathToFileURL(path.join(SHARED_PROVIDERS, '_registry.mjs')).href
);
const { makeHttpCtx } = await import(
  pathToFileURL(path.join(SHARED_PROVIDERS, '_http.mjs')).href
);
const { withConnectionScope } = await import(
  pathToFileURL(path.join(SHARED_PROVIDERS, '_connection-scope.mjs')).href
);

const HARD_MAX_STDIN_BYTES = 262_144;
const HARD_MAX_STDOUT_BYTES = 1_048_576;
const HARD_MAX_JOBS = 200;
const HARD_MAX_DESC = 4_000;
const HARD_MAX_REQUESTS = 25;
const HARD_MAX_TIMEOUT_MS = 20_000;

const UNSUPPORTED_VENDORS = [
  [/dayforcehcm\.com/i, 'dayforce'],
  [/employmenthero\.com/i, 'employmenthero'],
  [/scouttalent/i, 'scout'],
  [/expr3ss\.com/i, 'expr3ss'],
  [/applynow\.net\.au/i, 'applynow'],
];

function isHttpUrl(value) {
  if (typeof value !== 'string') return false;
  try {
    const parsed = new URL(value.trim());
    return parsed.protocol === 'http:' || parsed.protocol === 'https:';
  } catch {
    return false;
  }
}

function detectUnsupported(url) {
  for (const [re, vendor] of UNSUPPORTED_VENDORS) {
    if (re.test(url)) return vendor;
  }
  return null;
}

async function readStdinLimited(maxBytes) {
  const chunks = [];
  let total = 0;
  for await (const chunk of process.stdin) {
    const buf = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    total += buf.length;
    if (total > maxBytes) {
      throw new Error(`stdin exceeds ${maxBytes} bytes`);
    }
    chunks.push(buf);
  }
  return Buffer.concat(chunks).toString('utf8');
}

function coercePostedAt(val) {
  if (typeof val === 'number' && Number.isFinite(val)) return Math.trunc(val);
  if (typeof val === 'string' && val.trim()) {
    const num = Number(val);
    if (Number.isFinite(num)) return Math.trunc(num);
    const ms = Date.parse(val);
    if (Number.isFinite(ms)) return Math.trunc(ms / 1000);
  }
  return null;
}

function normalizeJob(raw, fallbackCompany) {
  if (!raw || typeof raw !== 'object') return null;
  const title = typeof raw.title === 'string' ? raw.title.trim() : '';
  const url = typeof raw.url === 'string' ? raw.url.trim() : '';
  if (!title || !isHttpUrl(url)) return null;
  const company = typeof raw.company === 'string' && raw.company.trim()
    ? raw.company.trim()
    : (fallbackCompany || '');
  const location = typeof raw.location === 'string' ? raw.location.trim() : '';
  let description = typeof raw.description === 'string' ? raw.description.trim() : null;
  if (description && description.length > HARD_MAX_DESC) {
    description = description.slice(0, HARD_MAX_DESC);
  }
  return {
    title: title.slice(0, 240),
    url,
    company: company.slice(0, 160),
    location: location.slice(0, 200),
    description: description || null,
    posted_at: coercePostedAt(raw.postedAt ?? raw.posted_at),
    external_id: typeof raw.externalId === 'string' ? raw.externalId : null,
  };
}

function writeCappedJson(payload, maxStdoutBytes) {
  const cap = Math.min(Math.max(512, maxStdoutBytes || HARD_MAX_STDOUT_BYTES), HARD_MAX_STDOUT_BYTES);
  let serialized = JSON.stringify(payload);
  if (Buffer.byteLength(serialized, 'utf8') > cap && Array.isArray(payload.jobs)) {
    const trimmed = { ...payload, jobs: [...payload.jobs], truncated: true };
    for (let i = 0; i < trimmed.jobs.length && Buffer.byteLength(serialized, 'utf8') > cap; i++) {
      trimmed.jobs[i] = { ...trimmed.jobs[i], description: null };
      serialized = JSON.stringify(trimmed);
    }
    while (trimmed.jobs.length > 0 && Buffer.byteLength(serialized, 'utf8') > cap) {
      trimmed.jobs.pop();
      serialized = JSON.stringify(trimmed);
    }
  }
  if (Buffer.byteLength(serialized, 'utf8') > cap) {
    serialized = JSON.stringify({ ok: false, error: 'Output exceeds byte limit', jobs: [], requests: payload.requests || 0 });
  }
  process.stdout.write(serialized);
}

async function main() {
  const providersDir = process.argv[2] ? path.resolve(process.argv[2]) : SHARED_PROVIDERS;
  let payload;
  try {
    const raw = await readStdinLimited(HARD_MAX_STDIN_BYTES);
    payload = JSON.parse(raw);
  } catch (err) {
    writeCappedJson({ ok: false, error: err?.message || 'Invalid JSON input' }, HARD_MAX_STDOUT_BYTES);
    process.exit(0);
  }

  const maxStdoutBytes = Number(payload?.max_stdout_bytes) || HARD_MAX_STDOUT_BYTES;
  const providers = await loadProviders(providersDir);

  if (payload?.mode === 'detect') {
    const urls = Array.isArray(payload.urls) ? payload.urls : [];
    const company = typeof payload.company === 'string' && payload.company.trim() ? payload.company.trim() : 'Candidate';
    const matches = [];
    for (const rawUrl of urls) {
      if (!isHttpUrl(rawUrl)) {
        matches.push({ url: String(rawUrl || ''), provider_id: null, supported: false, unsupported_vendor: null });
        continue;
      }
      const res = resolveProvider({ name: company, careers_url: rawUrl }, providers, { skipIds: ['local-parser'] });
      if (res && 'provider' in res && res.provider && res.provider.id !== 'local-parser') {
        matches.push({ url: rawUrl, provider_id: res.provider.id, supported: true, unsupported_vendor: null });
      } else {
        matches.push({ url: rawUrl, provider_id: null, supported: false, unsupported_vendor: detectUnsupported(rawUrl) });
      }
    }
    writeCappedJson({ ok: true, matches }, maxStdoutBytes);
    return;
  }

  if (payload?.mode === 'fetch') {
    const url = typeof payload.url === 'string' ? payload.url.trim() : '';
    const company = typeof payload.company === 'string' ? payload.company.trim() : '';
    const requestedId = typeof payload.provider_id === 'string' ? payload.provider_id.trim() : '';
    const apiUrl = typeof payload.api_url === 'string' ? payload.api_url.trim() : '';
    const maxJobs = Math.min(Math.max(1, Number(payload.max_jobs) || HARD_MAX_JOBS), HARD_MAX_JOBS);
    const maxRequests = Math.min(Math.max(1, Number(payload.max_requests) || HARD_MAX_REQUESTS), HARD_MAX_REQUESTS);
    const timeoutMs = Math.min(Math.max(200, Number(payload.timeout_ms) || HARD_MAX_TIMEOUT_MS), HARD_MAX_TIMEOUT_MS);

    if (requestedId === 'local-parser') {
      writeCappedJson({ ok: false, provider_id: requestedId, jobs: [], error: 'local-parser is disallowed', requests: 0 }, maxStdoutBytes);
      return;
    }
    if (!isHttpUrl(url)) {
      writeCappedJson({ ok: false, provider_id: requestedId, jobs: [], error: 'Invalid careers URL', requests: 0 }, maxStdoutBytes);
      return;
    }

    const entry = {
      name: company || 'Employer',
      careers_url: url,
      ...(requestedId ? { provider: requestedId } : {}),
      ...(apiUrl ? { api: apiUrl } : {}),
    };
    const resolved = resolveProvider(entry, providers, { skipIds: ['local-parser'] });
    if (!resolved || 'error' in resolved || !resolved.provider || resolved.provider.id === 'local-parser') {
      const msg = resolved && 'error' in resolved ? resolved.error : 'No supported ATS provider matched';
      writeCappedJson({ ok: false, provider_id: requestedId, jobs: [], error: msg, requests: 0 }, maxStdoutBytes);
      return;
    }
    const provider = resolved.provider;
    if (requestedId && provider.id !== requestedId) {
      writeCappedJson({ ok: false, provider_id: requestedId, jobs: [], error: `Provider mismatch: ${provider.id}`, requests: 0 }, maxStdoutBytes);
      return;
    }

    let requests = 0;
    const ctx = makeHttpCtx({
      onRequest() {
        requests += 1;
        if (requests > maxRequests) {
          throw new Error(`Request budget exceeded (${maxRequests})`);
        }
      },
    });

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(new Error(`ATS timeout after ${timeoutMs}ms`)), timeoutMs);
    try {
      const rawJobs = await Promise.race([
        withConnectionScope(async () => provider.fetch(entry, ctx), { signal: controller.signal }),
        new Promise((_, reject) => {
          controller.signal.addEventListener('abort', () => reject(controller.signal.reason || new Error('Aborted')), { once: true });
        }),
      ]);
      if (!Array.isArray(rawJobs)) {
        writeCappedJson({ ok: false, provider_id: provider.id, jobs: [], error: 'Provider returned non-array payload', requests }, maxStdoutBytes);
        return;
      }
      const normalized = [];
      for (const item of rawJobs) {
        if (normalized.length >= maxJobs) break;
        const job = normalizeJob(item, company);
        if (job) normalized.push(job);
      }
      writeCappedJson({ ok: true, provider_id: provider.id, jobs: normalized, error: null, requests }, maxStdoutBytes);
    } catch (err) {
      writeCappedJson({ ok: false, provider_id: provider.id, jobs: [], error: String(err?.message || err), requests }, maxStdoutBytes);
    } finally {
      clearTimeout(timer);
    }
    return;
  }

  writeCappedJson({ ok: false, error: 'Unsupported bridge mode' }, maxStdoutBytes);
}

main().catch((err) => {
  writeCappedJson({ ok: false, error: String(err?.message || err), jobs: [], requests: 0 }, HARD_MAX_STDOUT_BYTES);
  process.exit(0);
});
