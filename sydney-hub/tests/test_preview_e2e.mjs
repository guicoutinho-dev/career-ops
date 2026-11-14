import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const args = process.argv.slice(2);
const baseUrl = args.find(arg => !arg.startsWith('--')) || 'http://127.0.0.1:8795';
const modeArg = args.find(arg => arg.startsWith('--mode=')) || '--mode=leads-only';
const mode = modeArg.split('=')[1];
if (!['leads-only', 'mixed'].includes(mode)) {
  throw new Error(`Unsupported --mode=${mode}; expected leads-only or mixed`);
}

function attachErrorCollectors(page, pageErrors, consoleErrors) {
  page.on('pageerror', err => pageErrors.push(err.message));
  page.on('console', msg => {
    if (msg.type() === 'error') {
      const text = msg.text();
      if (!text.includes('favicon.ico')) {
        consoleErrors.push(text);
      }
    }
  });
}

async function assertNoHorizontalOverflow(page, label) {
  const hasOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth
  );
  assert.equal(hasOverflow, false, `Expected zero horizontal overflow at ${label}`);
}

const browser = await chromium.launch({ headless: true });

try {
  const pageErrors = [];
  const consoleErrors = [];

  // 1. Desktop 1440x900
  const desktop = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await desktop.addInitScript(() => {
    localStorage.setItem('sydney_theme', 'dark');
  });
  const page = await desktop.newPage();
  attachErrorCollectors(page, pageErrors, consoleErrors);

  const summaryResp = await page.request.get(`${baseUrl}/api/opportunities/summary`);
  assert.equal(summaryResp.status(), 200);
  const summary = await summaryResp.json();
  const counts = summary.counts_by_state;

  if (mode === 'leads-only') {
    assert.equal(counts.verified_job, 0);
    assert.equal(counts.unverified_job_post, 0);
    assert.equal(counts.employer_lead, 155);
  } else {
    assert.equal(counts.verified_job, 0);
    assert.ok(counts.unverified_job_post > 0, 'Expected community posts in mixed mode');
    assert.ok(counts.employer_lead > 0, 'Expected employer leads in mixed mode');
  }

  await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });
  await page.locator('#jobsSegmentTabs').waitFor();
  await page.locator('.discovery-empty').waitFor({ timeout: 15000 });
  await assertNoHorizontalOverflow(page, '1440x900 initial');

  // Segment counts match /api/opportunities/summary
  assert.equal(await page.locator('#segCountVerified').textContent(), String(counts.verified_job));
  assert.equal(await page.locator('#segCountCommunity').textContent(), String(counts.unverified_job_post));
  assert.equal(await page.locator('#segCountLead').textContent(), String(counts.employer_lead));

  // Honest empty state on Vagas verificadas
  assert.ok(
    await page.locator('.discovery-empty').getByText('Nenhuma vaga verificada carregada ainda').isVisible()
  );
  assert.equal(await page.locator('.job-card[data-verification="verified_job"]').count(), 0);

  if (mode === 'mixed') {
    await page.locator('#jobsSegmentTabs button[data-segment="unverified_job_post"]').click();
    await page.locator('.job-card[data-verification="unverified_job_post"]').first().waitFor();
    const communityCount = await page.locator('.job-card[data-verification="unverified_job_post"]').count();
    assert.equal(communityCount, counts.unverified_job_post);

    await page.locator('#btnOpenJobsFilters').click();
    await page.locator('#jobsFiltersDialog[open]').waitFor();
    await page.locator('#chkIncludeUnknownType').check();
    await page.locator('#chkIncludeUnknownType').uncheck();
    await page.locator('#jobsFiltersDialog form button[aria-label="Fechar filtros"]').click();

    await page.locator('#jobsSearchInput').fill('Barista');
    const narrowedCommunity = await page.locator('.job-card').count();
    assert.ok(narrowedCommunity <= communityCount);
    await page.locator('#jobsSearchInput').fill('');
    assert.equal(await page.locator('.job-card').count(), counts.unverified_job_post);
  }

  // Switch to Empresas para prospecção
  await page.locator('#jobsSegmentTabs button[data-segment="employer_lead"]').click();
  await page.locator('.job-card[data-verification="employer_lead"]').first().waitFor({ timeout: 15000 });
  assert.equal(
    await page.locator('.job-card[data-verification="employer_lead"]').count(),
    counts.employer_lead
  );
  assert.equal(await page.locator('.job-card[data-verification="verified_job"]').count(), 0);

  await page.locator('#jobsMapLoading').waitFor({ state: 'hidden', timeout: 20000 });
  const markerCount = await page.locator('.map-pill-marker').count();
  assert.ok(markerCount >= 1, `Expected at least 1 map pill marker, got ${markerCount}`);

  // Click an employer_lead card on desktop -> opens #appInspector cleanly with honest prospection UI
  const firstLeadCard = page.locator('.job-card[data-verification="employer_lead"]').first();
  const firstCompany = (await firstLeadCard.locator('.job-card-company, .job-card-title').first().textContent())?.trim() || '';
  await firstLeadCard.locator('.job-card-open').click();
  assert.equal(await page.locator('#appInspector').evaluate(el => !el.classList.contains('collapsed')), true);
  assert.ok(await page.locator('#insBody').getByText('Empresa para prospecção').first().isVisible());
  assert.ok(await page.locator('#insBody').getByRole('link', { name: 'Abrir site / carreira ↗' }).isVisible());
  assert.ok(await page.locator('#insBody').getByRole('button', { name: 'Registrar contato' }).isVisible());
  assert.equal(await page.locator('#insBody').getByRole('button', { name: 'Registrar candidatura' }).count(), 0);

  // Collapsing #appInspector expands #jobsMapPane width (> 100px)
  const openMapW = await page.locator('#jobsMapPane').evaluate(el => el.getBoundingClientRect().width);
  await page.locator('#appInspector button[title^="Fechar"], #appInspector button[title^="Close"]').click();
  assert.equal(await page.locator('#appInspector').evaluate(el => el.classList.contains('collapsed')), true);
  await page.waitForTimeout(260);
  const closedMapW = await page.locator('#jobsMapPane').evaluate(el => el.getBoundingClientRect().width);
  assert.ok(closedMapW > openMapW + 100, `Expected map width to expand (${openMapW} -> ${closedMapW})`);

  // Search narrows results + clearing restores exact lead count
  const searchToken = firstCompany.split(/\s+/)[0] || 'Rockdale';
  await page.locator('#jobsSearchInput').fill(searchToken);
  const filteredCount = await page.locator('.job-card[data-verification="employer_lead"]').count();
  assert.ok(filteredCount >= 1 && filteredCount <= counts.employer_lead);
  await page.locator('#jobsSearchInput').fill('');
  assert.equal(
    await page.locator('.job-card[data-verification="employer_lead"]').count(),
    counts.employer_lead
  );

  // Filters dialog opens and closes cleanly
  await page.locator('#btnOpenJobsFilters').click();
  await page.locator('#jobsFiltersDialog[open]').waitFor();
  await page.locator('#jobsFiltersDialog form button[aria-label="Fechar filtros"]').click();
  assert.equal(await page.locator('#jobsFiltersDialog').evaluate(el => el.hasAttribute('open')), false);

  // Favorite + note persists in localStorage without mutating DB
  await firstLeadCard.locator('.job-card-open').click();
  await page.locator('#insBody .job-row-bookmark').click();
  await page.locator('#insBody textarea').fill('Contato preview E2E');
  const selectedId = await page.evaluate(() => state.jobs.selectedId);
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.locator('#jobsSegmentTabs button[data-segment="employer_lead"]').click();
  await page.locator('.job-card[data-verification="employer_lead"]').first().waitFor();
  assert.equal(await page.evaluate(id => state.jobFavorites[id], selectedId), true);
  assert.equal(await page.evaluate(id => state.jobNotes[id], selectedId), 'Contato preview E2E');

  // 2. Desktop 1280x800 (dark + light theme)
  await page.setViewportSize({ width: 1280, height: 800 });
  await assertNoHorizontalOverflow(page, '1280x800 dark');
  await page.locator('#themeToggleBtn').click();
  assert.equal(await page.evaluate(() => document.documentElement.getAttribute('data-theme')), 'light');
  await assertNoHorizontalOverflow(page, '1280x800 light');
  await page.locator('#themeToggleBtn').click();

  // 3. Desktop 1100x800
  await page.setViewportSize({ width: 1100, height: 800 });
  await page.locator('.job-card[data-verification="employer_lead"]').first().locator('.job-card-open').click();
  assert.equal(
    await page.locator('#appInspector').evaluate(
      el => !el.classList.contains('collapsed') && getComputedStyle(el).display !== 'none'
    ),
    true
  );
  await assertNoHorizontalOverflow(page, '1100x800');

  // 4. Stays, Tracker, Automation workspace switches render without error
  await page.locator('#wsBtnStays').click();
  assert.ok(await page.locator('#staysView').isVisible());
  await page.locator('#wsBtnTracker').click();
  assert.ok(await page.locator('#trackerView').isVisible());
  await page.locator('#wsBtnAutomation').click();
  assert.ok(await page.locator('#sourcesView').isVisible());
  await page.locator('#wsBtnJobs').click();
  assert.ok(await page.locator('#jobsView').isVisible());
  await desktop.close();

  // 5. Mobile 390x844
  const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true });
  const phone = await mobile.newPage();
  attachErrorCollectors(phone, pageErrors, consoleErrors);
  await phone.goto(baseUrl, { waitUntil: 'domcontentloaded' });
  await phone.locator('#jobsSegmentTabs button[data-segment="employer_lead"]').click();
  await phone.locator('.job-card[data-verification="employer_lead"]').first().waitFor({ timeout: 15000 });
  await assertNoHorizontalOverflow(phone, '390x844 mobile');

  await phone.locator('.job-card[data-verification="employer_lead"] .job-card-open').first().click();
  await phone.locator('#jobsDetailDrawer.open').waitFor();
  assert.equal(await phone.locator('#appInspector').evaluate(el => getComputedStyle(el).display), 'none');
  await phone.keyboard.press('Escape');
  assert.equal(await phone.locator('#jobsDetailDrawer').evaluate(el => el.classList.contains('open')), false);
  await mobile.close();

  assert.deepEqual(pageErrors, [], `Unexpected pageerror events: ${JSON.stringify(pageErrors)}`);
  assert.deepEqual(consoleErrors, [], `Unexpected console.error events: ${JSON.stringify(consoleErrors)}`);
  console.log(`Preview E2E (${mode}) passed: ${counts.employer_lead} leads, ${counts.unverified_job_post} community, ${counts.verified_job} verified.`);
} finally {
  await browser.close();
}
