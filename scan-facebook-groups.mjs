#!/usr/bin/env node
// @ts-check
/**
 * scan-facebook-groups.mjs — reads recent posts from Facebook groups the user
 * is ALREADY a member of, using the user's own logged-in browser profile.
 *
 * Why a real browser: m.facebook.com / mbasic are gone and plain HTTP with
 * copied cookies is the fastest way to get an account checkpointed. A
 * persistent Chromium profile (the user logs in by hand, once) behaves like
 * the user's normal browsing.
 *
 * Risk note: Meta's terms forbid automated collection. A real browser lowers
 * the detection risk but does not remove it. The scanner is deliberately slow
 * (few groups, few scrolls, random pauses) and read-only: it never posts,
 * comments, reacts, messages or joins anything.
 *
 * No bypass (providers/ADDING_A_PROVIDER.md §2, rule 3): if Facebook shows a
 * login wall, a checkpoint, or any CAPTCHA / "confirm it's you" screen, the
 * run STOPS immediately. It never solves, waits out or retries a challenge;
 * the user resolves it by hand in a normal browser.
 *
 * Post text is untrusted data (AGENTS.md "Untrusted External Content"): it is
 * stored and scored, never executed or obeyed.
 *
 * Usage:
 *   node scan-facebook-groups.mjs --login        # opens a window, log in by hand, then close it
 *   node scan-facebook-groups.mjs                # scan enabled groups (visible window)
 *   node scan-facebook-groups.mjs --headless     # scan without a window
 *   node scan-facebook-groups.mjs --group "Cairns" --scrolls 3
 *   node scan-facebook-groups.mjs --dry-run      # scan + print, don't write output
 *
 * Output: output/facebook_posts.json (merged + deduplicated across runs).
 * Then: python scripts/ingest_facebook_posts.py  → SQLite as NEEDS_REVIEW.
 */

import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import yaml from 'js-yaml';

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const CONFIG_PATH = path.join(ROOT, 'config', 'facebook_groups.yaml');
const PROFILE_DIR = path.join(ROOT, 'data', 'facebook-profile'); // gitignored (data/*)
const OUTPUT_PATH = path.join(ROOT, 'output', 'facebook_posts.json'); // gitignored (output/*)

// ---------------------------------------------------------------- args
const argv = process.argv.slice(2);
const flag = (name) => argv.includes(name);
const opt = (name, fallback) => {
  const i = argv.indexOf(name);
  return i !== -1 && argv[i + 1] ? argv[i + 1] : fallback;
};
const LOGIN = flag('--login');
const HEADLESS = flag('--headless');
const DRY_RUN = flag('--dry-run');
const GROUP_FILTER = opt('--group', '').toLowerCase();

class ChallengeError extends Error {}

// ---------------------------------------------------------------- helpers
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const jitter = (min, max) => sleep(min + Math.random() * (max - min));

function loadConfig() {
  const cfg = /** @type {any} */ (yaml.load(fs.readFileSync(CONFIG_PATH, 'utf8')));
  return {
    groups: (cfg.groups || []).filter((g) => g.enabled !== false),
    keywords: cfg.keywords || {},
    limits: {
      maxGroups: Number(cfg.limits?.max_groups_per_run ?? 6),
      scrolls: Number(opt('--scrolls', cfg.limits?.scrolls_per_group ?? 5)),
    },
  };
}

/** Stop on anything that is not a normal, logged-in group page. */
async function assertNoChallenge(page) {
  const url = page.url();
  if (/\/login|\/checkpoint|\/two_step_verification|captcha/i.test(url)) {
    throw new ChallengeError(`Facebook redirected to ${url}`);
  }
  const blocked = await page
    .locator('text=/confirm (it\'s|that it\'s) you|security check|enter the characters|verify your account|confirme que é você|verificação de segurança/i')
    .count();
  if (blocked > 0) throw new ChallengeError(`Challenge screen on ${url}`);
  if (await page.locator('iframe[src*="captcha"], iframe[title*="captcha" i]').count()) {
    throw new ChallengeError(`CAPTCHA frame on ${url}`);
  }
}

const PHONE_RE = /(?:\+?61\s?|0)4\d{2}[\s-]?\d{3}[\s-]?\d{3}|(?:\+?61\s?|0)[2378][\s-]?\d{4}[\s-]?\d{4}/g;
const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g;
const POSTCODE_RE = /\b(0[89]\d{2}|[1-7]\d{3})\b/;

function classify(text, kw) {
  const t = text.toLowerCase();
  const noise = (kw.exclude_noise || []).find((k) => t.includes(k.toLowerCase()));
  if (noise) return { keep: false, reason: `noise:${noise}` };
  const hits = (kw.must_match_one || []).filter((k) => t.includes(k.toLowerCase()));
  if (!hits.length) return { keep: false, reason: 'no_job_keyword' };
  const boosts = (kw.priority_boost || []).filter((k) => t.includes(k.toLowerCase()));
  return { keep: true, hits, boosts };
}

// ---------------------------------------------------------------- scraping
async function expandSeeMore(page) {
  const buttons = page.locator('div[role="button"]:text-matches("^(See more|Ver mais)$", "i")');
  const n = Math.min(await buttons.count(), 15);
  for (let i = 0; i < n; i++) {
    try {
      await buttons.nth(i).click({ timeout: 1500 });
      await jitter(250, 600);
    } catch { /* already expanded or detached */ }
  }
}

async function extractPosts(page) {
  return page.evaluate(() => {
    const out = [];
    const articles = document.querySelectorAll('div[role="feed"] div[role="article"]');
    for (const a of articles) {
      // Skip nested comment articles.
      if (a.parentElement?.closest('div[role="article"]')) continue;
      const msg = a.querySelector('[data-ad-preview="message"], [data-ad-comet-preview="message"]');
      const text = (msg || a).innerText || '';
      const link = [...a.querySelectorAll('a[href]')]
        .map((x) => x.getAttribute('href') || '')
        .find((h) => /\/groups\/[^/]+\/(posts|permalink)\/\d+/.test(h));
      const author = a.querySelector('h2 a, h3 a, strong a')?.textContent?.trim() || '';
      out.push({ text: text.trim(), link: link || '', author });
    }
    return out;
  });
}

async function scanGroup(page, group, cfg) {
  const url = group.url.replace(/\/$/, '') + '/?sorting_setting=CHRONOLOGICAL';
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await jitter(3000, 5000);
  await assertNoChallenge(page);

  if (!(await page.locator('div[role="feed"]').count())) {
    return { group: group.name, status: 'no_feed', posts: [] };
  }

  const seen = new Map();
  for (let s = 0; s < cfg.limits.scrolls; s++) {
    await expandSeeMore(page);
    for (const p of await extractPosts(page)) {
      if (p.text.length < 40) continue;
      const key = p.link || crypto.createHash('sha1').update(p.text).digest('hex');
      if (!seen.has(key)) seen.set(key, p);
    }
    await page.mouse.wheel(0, 1800 + Math.random() * 1200);
    await jitter(2500, 5500);
    await assertNoChallenge(page);
  }

  const posts = [];
  for (const [key, p] of seen) {
    const c = classify(p.text, cfg.keywords);
    if (!c.keep) continue;
    const pc = p.text.match(POSTCODE_RE);
    posts.push({
      id: 'fb_' + crypto.createHash('sha1').update(key).digest('hex').slice(0, 16),
      group: group.name,
      group_url: group.url,
      category: group.category,
      author: p.author,
      permalink: p.link ? new URL(p.link, 'https://www.facebook.com').href.split('?')[0] : '',
      text: p.text.slice(0, 4000),
      phones: [...new Set(p.text.match(PHONE_RE) || [])],
      emails: [...new Set(p.text.match(EMAIL_RE) || [])],
      postcode: pc ? pc[1] : null,
      keyword_hits: c.hits,
      priority_boosts: c.boosts,
      scraped_at: new Date().toISOString(),
    });
  }
  return { group: group.name, status: 'ok', scanned: seen.size, posts };
}

// ---------------------------------------------------------------- main
async function main() {
  fs.mkdirSync(PROFILE_DIR, { recursive: true });

  if (LOGIN) {
    const ctx = await chromium.launchPersistentContext(PROFILE_DIR, { headless: false, viewport: null });
    const page = ctx.pages()[0] || (await ctx.newPage());
    await page.goto('https://www.facebook.com/login');
    console.log('Log in to Facebook in the window that opened (complete any 2FA yourself).');
    console.log('Close the browser window when you see your feed — the session is saved in data/facebook-profile/.');
    await new Promise((r) => ctx.on('close', r));
    return;
  }

  const cfg = loadConfig();
  let groups = cfg.groups;
  if (GROUP_FILTER) groups = groups.filter((g) => g.name.toLowerCase().includes(GROUP_FILTER));
  groups = groups.slice(0, cfg.limits.maxGroups);
  if (!groups.length) {
    console.error('No enabled groups match. Edit config/facebook_groups.yaml.');
    process.exit(1);
  }

  const ctx = await chromium.launchPersistentContext(PROFILE_DIR, {
    headless: HEADLESS,
    viewport: { width: 1280, height: 900 },
    locale: 'en-AU',
  });
  const page = ctx.pages()[0] || (await ctx.newPage());

  const results = [];
  try {
    for (const [i, g] of groups.entries()) {
      process.stdout.write(`[${i + 1}/${groups.length}] ${g.name} ... `);
      try {
        const r = await scanGroup(page, g, cfg);
        results.push(r);
        console.log(`${r.status} — ${r.scanned ?? 0} posts read, ${r.posts.length} job-like`);
      } catch (e) {
        if (e instanceof ChallengeError) throw e;
        results.push({ group: g.name, status: 'error', error: String(e.message || e), posts: [] });
        console.log(`error: ${e.message || e}`);
      }
      if (i < groups.length - 1) await jitter(15000, 30000);
    }
  } catch (e) {
    if (e instanceof ChallengeError) {
      console.error(`\nSTOPPED: ${e.message}`);
      console.error('Facebook wants a login or a security check. Resolve it by hand in a normal browser');
      console.error('(or run --login again), then re-run. Nothing was bypassed or retried.');
      await ctx.close();
      process.exit(2);
    }
    throw e;
  }
  await ctx.close();

  const fresh = results.flatMap((r) => r.posts);
  let existing = [];
  if (fs.existsSync(OUTPUT_PATH)) {
    try { existing = JSON.parse(fs.readFileSync(OUTPUT_PATH, 'utf8')); } catch { existing = []; }
  }
  const byId = new Map(existing.map((p) => [p.id, p]));
  let added = 0;
  for (const p of fresh) {
    if (!byId.has(p.id)) added++;
    byId.set(p.id, { ...byId.get(p.id), ...p });
  }

  console.log(`\n${fresh.length} job-like posts this run, ${added} new.`);
  if (DRY_RUN) {
    for (const p of fresh.slice(0, 10)) console.log(`- [${p.group}] ${p.text.slice(0, 120).replace(/\s+/g, ' ')}`);
    return;
  }
  fs.mkdirSync(path.dirname(OUTPUT_PATH), { recursive: true });
  fs.writeFileSync(OUTPUT_PATH, JSON.stringify([...byId.values()], null, 2));
  console.log(`Saved → ${path.relative(ROOT, OUTPUT_PATH)}. Next: python scripts/ingest_facebook_posts.py`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
