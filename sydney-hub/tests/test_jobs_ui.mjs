import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const baseUrl = process.argv[2] || 'http://127.0.0.1:8767';
const browser = await chromium.launch({ headless: true });

try {
  const desktop = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await desktop.addInitScript(() => {
    localStorage.setItem('sydney_theme', 'dark');
    if (!localStorage.getItem('sydney_catalog_fixture_v1')) {
      localStorage.setItem('sydney_jobs_status_v1', JSON.stringify({ 'legacy-barista': 'fav' }));
      localStorage.setItem('sydney_jobs_notes_v1', JSON.stringify({ 'legacy-barista': 'Contato anterior' }));
      localStorage.setItem('sydney_catalog_fixture_v1', 'yes');
    }
  });
  const page = await desktop.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(baseUrl);
  await page.locator('.job-card').first().waitFor();

  // Task 3: Three-segment tabs & strict separation
  const tabVerified = page.locator('#jobsSegmentTabs button[data-segment="verified_job"]');
  const tabCommunity = page.locator('#jobsSegmentTabs button[data-segment="unverified_job_post"]');
  const tabLeads = page.locator('#jobsSegmentTabs button[data-segment="employer_lead"]');
  assert.equal(await tabVerified.getAttribute('aria-selected'), 'true');
  assert.match((await tabVerified.textContent()) || '', /3/);
  assert.match((await tabCommunity.textContent()) || '', /1/);
  assert.match((await tabLeads.textContent()) || '', /2/);

  assert.equal(await page.locator('.job-card').count(), 3);
  assert.equal(await page.locator('.job-card[data-verification="verified_job"]').count(), 3);
  assert.equal(await page.locator('#jobsSuburbFilter option[value="Rockdale"]').count(), 1);
  assert.equal(await page.locator('#jobFacetFilters button[data-filter="region"][data-value="NSW"]').count(), 1);

  // Strict null filtering + "Incluir não informado" opt-ins inside verified_job
  await page.locator('#btnOpenJobsFilters').click();
  await page.locator('#jobFacetFilters button[data-filter="type"][data-value="Casual"]').click();
  assert.equal(await page.locator('.job-card').count(), 1);
  await page.locator('#chkIncludeUnknownType').check();
  assert.equal(await page.locator('.job-card').count(), 3);
  await page.locator('#chkIncludeUnknownType').uncheck();
  await page.locator('#jobFacetFilters button[data-filter="type"][data-value="Casual"]').click();
  assert.equal(await page.locator('.job-card').count(), 3);

  await page.locator('#jobFacetFilters button[data-filter="req"][data-value="RSA"]').click();
  assert.equal(await page.locator('.job-card').count(), 1);
  await page.locator('#chkIncludeUnknownReq').check();
  assert.equal(await page.locator('.job-card').count(), 3);
  await page.locator('#chkIncludeUnknownReq').uncheck();
  await page.locator('#jobFacetFilters button[data-filter="req"][data-value="RSA"]').click();

  await page.locator('#jobFacetFilters button[data-filter="salaryMin"][data-value="30"]').click();
  assert.equal(await page.locator('.job-card').count(), 1);
  await page.locator('#chkIncludeUnknownPay').check();
  assert.equal(await page.locator('.job-card').count(), 3);
  await page.locator('#chkIncludeUnknownPay').uncheck();
  await page.locator('#jobsFiltersDialog form button[aria-label="Fechar filtros"]').click();
  await page.locator('#btnResetAllJobFilters').click();
  assert.equal(await page.locator('.job-card').count(), 3);

  // Task 4: Compact cards (<= 3 tags) + non-color map pins + bidirectional sync
  const tagCounts = await page.locator('.job-card').evaluateAll(cards =>
    cards.map(c => c.querySelectorAll('.job-card-tags .job-tag').length)
  );
  assert.ok(tagCounts.every(n => n >= 1 && n <= 3), `Expected 1..3 tags per card, got ${tagCounts}`);
  await page.locator('#jobsMapLoading').waitFor({ state: 'hidden', timeout: 20000 });
  const pinMeta = await page.locator('.map-pill-marker[data-verification]').evaluateAll(pins =>
    pins.map(p => ({ ver: p.dataset.verification, hasGlyph: Boolean(p.querySelector('.marker-glyph')), label: p.getAttribute('aria-label') || '' }))
  );
  assert.ok(pinMeta.length >= 1, 'Expected at least 1 map pill marker with data-verification');
  assert.ok(pinMeta.every(m => ['verified_job', 'unverified_job_post', 'employer_lead', 'mixed'].includes(m.ver) && m.hasGlyph && m.label.length > 0));
  const cafeMarker = page.locator('.map-pill-marker[data-geo-key]').filter({ hasText: 'Rockdale' }).first();
  if (await cafeMarker.count()) {
    await cafeMarker.click();
    assert.ok((await page.locator('.job-card.selected .job-card-open').getAttribute('aria-pressed')) === 'true');
    if (await page.locator('#jobsDetailDrawer.open').count()) {
      await page.locator('#jobsDetailDrawer button[title^="Fechar detalhes"]').click();
    }
  }

  // Task 5: Desktop unified collapsible #appInspector (no modal backdrop blocking map)
  await page.getByRole('button', { name: /Ver detalhes: Bar Staff — Mascot Bar/ }).click();
  assert.equal(await page.locator('#jobsDetailDrawer').evaluate(el => el.classList.contains('open')), false);
  assert.equal(await page.locator('#appInspector').evaluate(el => !el.classList.contains('collapsed')), true);
  assert.ok(await page.locator('#insBody').getByText('Recente').isVisible());
  assert.ok(await page.locator('#insBody').getByText('Trajeto não verificado').first().isVisible());
  assert.equal(await page.locator('#insBody .jobs-inspector-actions a').getAttribute('href'), 'https://apply.workable.com/fixture/j/PUB1/');
  const publishedId = await page.evaluate(() => getAllJobs().find(job => job.url === 'https://apply.workable.com/fixture/j/PUB1/').id);
  await page.locator('#insBody .job-row-bookmark').click();
  await page.locator('#insBody textarea').fill('Nota da vaga publicada');
  await page.locator('#insBody').getByRole('button', { name: 'Registrar candidatura' }).click();
  await page.reload();
  await page.locator('.job-card').first().waitFor();
  assert.equal(await page.evaluate(id => state.jobStatus[id], publishedId), 'applied');
  assert.equal(await page.evaluate(id => state.jobFavorites[id], publishedId), true);
  await page.getByRole('button', { name: /Ver detalhes: Bar Staff — Mascot Bar/ }).click();
  assert.equal(await page.locator('#insBody textarea').inputValue(), 'Nota da vaga publicada');

  // Closing #appInspector expands #jobsMapCanvas
  const mapWidthWithInspector = await page.locator('#jobsMapPane').evaluate(el => el.getBoundingClientRect().width);
  await page.locator('#appInspector button[title^="Fechar"], #appInspector button[title^="Close"]').click();
  assert.equal(await page.locator('#appInspector').evaluate(el => el.classList.contains('collapsed')), true);
  await page.waitForTimeout(260);
  const mapWidthCollapsed = await page.locator('#jobsMapPane').evaluate(el => el.getBoundingClientRect().width);
  assert.ok(mapWidthCollapsed > mapWidthWithInspector + 100, `Expected map width to expand cleanly (${mapWidthWithInspector} -> ${mapWidthCollapsed})`);

  // Stale verified_job (>14 days) discovered directly on employer website
  await page.getByRole('button', { name: /Ver detalhes: Barista — Cafe Website/ }).click();
  assert.ok(await page.locator('#insBody').getByText('Vaga verificada').count() > 0);
  assert.ok(await page.locator('#insBody').getByText(/Verificada em \d{2}\/\d{2}\/\d{4}/).isVisible());
  assert.ok(await page.locator('#insBody').getByText('Evidência >14 dias — revalidar').isVisible());
  assert.equal(await page.locator('#insBody .jobs-inspector-actions a').getAttribute('href'), 'https://cafe.example/careers/barista');
  assert.ok(await page.locator('#insBody').getByRole('link', { name: /OpenStreetMap contributors/ }).isVisible());
  const verifiedId = await page.evaluate(() => getAllJobs().find(job => job.url === 'https://cafe.example/careers/barista').id);
  await page.locator('#insBody textarea').fill('Verificada em teste');
  await page.locator('#insBody').getByRole('button', { name: 'Registrar candidatura' }).click();
  await page.reload();
  await page.locator('.job-card').first().waitFor();
  assert.equal(await page.evaluate(id => state.jobStatus[id], verifiedId), 'applied');
  await page.getByRole('button', { name: /Ver detalhes: Barista — Cafe Website/ }).click();
  assert.equal(await page.locator('#insBody textarea').inputValue(), 'Verificada em teste');
  await page.locator('#appInspector button[title^="Fechar"], #appInspector button[title^="Close"]').click();

  // Fresh ATS-sourced verified_job discovered via OSM keeps ODbL attribution while showing PageUp source
  await page.getByRole('button', { name: /Ver detalhes: Housekeeper — Harbour Hotel/ }).click();
  assert.ok(await page.locator('#insBody').getByText(/Verificada em \d{2}\/\d{2}\/\d{4}/).isVisible());
  assert.equal(await page.locator('#insBody').getByText('Evidência >14 dias — revalidar').count(), 0);
  assert.ok(await page.locator('#insBody').getByRole('link', { name: 'PageUp' }).isVisible());
  assert.ok(await page.locator('#insBody').getByRole('link', { name: /OpenStreetMap contributors/ }).isVisible());
  await page.locator('#appInspector button[title^="Fechar"], #appInspector button[title^="Close"]').click();
  assert.equal(await page.getByText('$99/h').count(), 0);
  await page.evaluate(() => openRockdaleReportPanel());
  assert.equal(await page.locator('#rockdaleReportContent .report-item-card').count(), 3);
  assert.ok(await page.locator('#rockdaleReportContent').getByText('Vaga verificada').isVisible());
  assert.ok(await page.locator('#rockdaleReportContent').getByText('Anúncio não verificado').isVisible());
  assert.equal(await page.locator('#rockdaleReportContent').getByText('$99/h').count(), 0);
  await page.evaluate(() => closeRockdaleReportModal());
  assert.equal(await page.locator('#jobsView').evaluate(el => el.classList.contains('list-only')), false);
  await page.locator('#jobsMapLoading').waitFor({ state: 'hidden', timeout: 20000 });

  await tabCommunity.click();
  assert.equal(await page.locator('.job-card').count(), 1);
  assert.equal(await page.locator('.job-card[data-verification="unverified_job_post"]').count(), 1);
  await page.locator('.job-card[data-verification="unverified_job_post"] .job-card-open').click();
  assert.equal(await page.locator('#appInspector').evaluate(el => !el.classList.contains('collapsed')), true);
  assert.equal(await page.locator('#jobsDrawerBody textarea').count(), 0);
  assert.equal(await page.locator('#insBody textarea').inputValue(), 'Contato anterior');
  assert.ok(await page.locator('#insBody .job-row-bookmark').evaluate(el => el.classList.contains('active')));
  await page.locator('#insBody textarea').fill('Nota preservada');
  await page.locator('#insBody').getByRole('button', { name: 'Registrar candidatura' }).click();
  assert.equal(await page.evaluate(() => state.jobStatus['legacy-barista']), 'applied');
  assert.equal(await page.evaluate(() => state.jobFavorites['legacy-barista']), true);
  await page.reload();
  await page.locator('#jobsSegmentTabs button[data-segment="unverified_job_post"]').click();
  await page.locator('.job-card').first().waitFor();
  assert.equal(await page.evaluate(() => state.jobStatus['legacy-barista']), 'applied');
  assert.equal(await page.evaluate(() => state.jobFavorites['legacy-barista']), true);
  await page.locator('.job-card[data-verification="unverified_job_post"] .job-card-open').click();
  assert.equal(await page.locator('#insBody textarea').inputValue(), 'Nota preservada');

  await page.locator('#jobsSegmentTabs button[data-segment="verified_job"]').click();
  await page.locator('#jobsSearchInput').fill('Barista');
  page.once('dialog', dialog => dialog.accept('Busca de teste'));
  await page.getByRole('button', { name: 'Salvar busca' }).click();
  await page.reload();
  await page.locator('.job-card').first().waitFor();
  assert.ok(await page.getByText('Busca de teste').count() > 0);
  await page.locator('#jobsSearchInput').fill('');
  await page.locator('#jobsSegmentTabs button[data-segment="employer_lead"]').click();
  assert.equal(await page.locator('.job-card').count(), 2);
  await page.getByRole('button', { name: /Ver detalhes: .*Hotel Fixture/ }).click();
  assert.ok(await page.locator('#insBody').getByText(/Empresa .*prospecção|sem vaga/i).first().isVisible());
  assert.equal(await page.locator('#insBody .jobs-inspector-actions a').getAttribute('href'), 'https://example.com/hotel');
  await page.locator('#insBody').getByRole('button', { name: 'Registrar contato' }).click();
  assert.equal(await page.evaluate(() => state.jobStatus['legacy-lead']), 'contacted');
  assert.equal(await page.evaluate(() => { discardJob(null, 'legacy-lead'); restoreLastDiscarded(); return state.jobStatus['legacy-lead']; }), 'contacted');
  await page.locator('#jobsSegmentTabs button[data-segment="verified_job"]').click();
  await page.locator('#wsBtnStays').click();
  assert.ok(await page.locator('#staysView').isVisible());
  assert.ok(await page.locator('#appInspector').evaluate(el => getComputedStyle(el).display !== 'none'));

  // Verify 1100px desktop width stays balanced & zero horizontal overflow
  await page.setViewportSize({ width: 1100, height: 800 });
  await page.locator('#wsBtnJobs').click();
  await page.locator('.job-card').first().click();
  assert.equal(await page.locator('#appInspector').evaluate(el => !el.classList.contains('collapsed') && getComputedStyle(el).display !== 'none'), true);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);

  const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true });
  const phone = await mobile.newPage();
  phone.on('pageerror', error => errors.push(error.message));
  await phone.goto(baseUrl);
  await phone.locator('.job-card').first().waitFor();
  assert.equal(await phone.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  assert.ok(await phone.locator('#jobsView').evaluate(el => el.classList.contains('list-only')));
  // Tapping card on mobile opens #jobsDetailDrawer bottom sheet while keeping #appInspector hidden
  await phone.locator('.job-card .job-card-open').first().click();
  assert.ok(await phone.locator('#jobsDetailDrawer').evaluate(el => el.classList.contains('open')));
  assert.equal(await phone.locator('#appInspector').evaluate(el => getComputedStyle(el).display), 'none');
  assert.ok(await phone.locator('#jobsDrawerBody').getByText('Recente').isVisible());
  await phone.locator('#jobsDetailDrawer button[title^="Fechar detalhes"]').click();
  await phone.locator('#viewBtnSplit').click();
  assert.equal(await phone.locator('#jobsMapPane').evaluate(el => getComputedStyle(el).display), 'block');
  await phone.locator('.map-pill-marker').first().waitFor();

  const failed = await browser.newContext();
  const offline = await failed.newPage();
  await offline.route('**/api/opportunities*', route => route.fulfill({ status: 503, body: 'unavailable' }));
  await offline.goto(baseUrl);
  assert.ok(await offline.getByText('Catálogo indisponível').isVisible());
  assert.equal(await offline.locator('.job-card').count(), 0);
  await offline.unroute('**/api/opportunities*');
  await offline.getByRole('button', { name: 'Tentar novamente' }).click();
  await offline.locator('.job-card').first().waitFor();
  assert.equal(await offline.locator('.job-card').count(), 3);
  assert.deepEqual(errors, []);
  await failed.close();

  const paged = await browser.newContext();
  const pagedPage = await paged.newPage();
  pagedPage.on('pageerror', error => errors.push(error.message));
  await pagedPage.route('**/api/opportunities*', route => {
    const reqUrl = new URL(route.request().url());
    if (reqUrl.pathname.endsWith('/summary')) {
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ total: 2, counts_by_state: { verified_job: 1, unverified_job_post: 1, employer_lead: 0 }, facets: {} }),
      });
    }
    const offset = Number(reqUrl.searchParams.get('offset') || '0');
    const item = offset === 0
      ? { id: 'p-1', title: 'Barista', company: 'Page One Cafe', source: 'Workable', discovery_source: 'Published Jobs', source_url: 'https://p1.example', suburb: 'Rockdale', location: 'Rockdale, NSW', state: 'NSW', verification_state: 'unverified_job_post', recent: true, commute_unknown: true }
      : { id: 'p-2', title: 'Chef', company: 'Page Two Bistro', source: 'PageUp', discovery_source: 'Published Jobs', source_url: 'https://p2.example', suburb: 'Mascot', location: 'Mascot, NSW', state: 'NSW', verification_state: 'verified_job', commute_unknown: false, commute_windows: { tuesday_0900: 40 }, commute_observations: [{ departure_window: 'tuesday_0900', checked_at: Date.now() / 1000, source_url: 'https://api.transport.nsw.gov.au/v1/tp/trip' }] };
    route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'application/json', 'X-Total-Count': '2' },
      body: JSON.stringify([item]),
    });
  });
  await pagedPage.goto(baseUrl);
  await pagedPage.locator('.job-card').first().waitFor();
  assert.equal(await pagedPage.locator('.job-card').count(), 1);
  await pagedPage.getByRole('button', { name: /Ver detalhes: Chef — Page Two Bistro/ }).click();
  assert.ok(await pagedPage.locator('#insBody').getByText(/terça 09h: 40 min · TfNSW medido em/).isVisible());
  await pagedPage.locator('#appInspector button[title^="Fechar"], #appInspector button[title^="Close"]').click();
  await pagedPage.locator('#jobsSegmentTabs button[data-segment="unverified_job_post"]').click();
  assert.equal(await pagedPage.locator('.job-card').count(), 1);
  assert.ok(await pagedPage.locator('.job-card[data-uid^="p-1"]').getByText('Recente').isVisible());
  assert.ok(await pagedPage.locator('.job-card[data-uid^="p-1"]').getByText('Trajeto não verificado').isVisible());
  assert.deepEqual(errors, []);
  await paged.close();

  // Honest empty state when 0 verified_job rows exist
  const leadsOnly = await browser.newContext();
  const leadsPage = await leadsOnly.newPage();
  leadsPage.on('pageerror', error => errors.push(error.message));
  await leadsPage.route('**/api/opportunities*', route => {
    const reqUrl = new URL(route.request().url());
    if (reqUrl.pathname.endsWith('/summary')) {
      return route.fulfill({ status: 200, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ total: 1, counts_by_state: { verified_job: 0, unverified_job_post: 0, employer_lead: 1 }, facets: {} }) });
    }
    return route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'application/json', 'X-Total-Count': '1' },
      body: JSON.stringify([{ id: 'only-lead', title: null, company: 'Lead Only Cafe', source: 'OpenStreetMap discovery', discovery_source: 'OpenStreetMap discovery', source_url: 'https://lead.example', suburb: 'Rockdale', location: 'Rockdale, NSW', state: 'NSW', verification_state: 'employer_lead' }]),
    });
  });
  await leadsPage.goto(baseUrl);
  await leadsPage.locator('.discovery-empty').waitFor();
  await leadsPage.getByRole('button', { name: /Empresas para prospecção/ }).last().click();
  await leadsPage.locator('.job-card[data-verification="employer_lead"]').first().waitFor();
  assert.equal(await leadsPage.locator('.job-card').count(), 1);
  assert.deepEqual(errors, []);
  await leadsOnly.close();

  // Task 6: Skeleton loading state, stale-selection guard, keyboard nav & mobile Esc focus restore
  const a11yCtx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const a11yPage = await a11yCtx.newPage();
  a11yPage.on('pageerror', error => errors.push(error.message));
  let releaseDelayedApi;
  const delayedGate = new Promise(resolve => { releaseDelayedApi = resolve; });
  await a11yPage.route('**/api/opportunities*', async route => {
    await delayedGate;
    await route.continue();
  });
  const navPromise = a11yPage.goto(baseUrl);
  await a11yPage.locator('#jobsListContainer[aria-busy="true"]').waitFor({ timeout: 5000 });
  assert.ok(await a11yPage.locator('.jobs-skeleton-card').count() >= 3, 'Expected skeleton cards while API is loading');
  releaseDelayedApi();
  await navPromise;
  await a11yPage.unroute('**/api/opportunities*');
  await a11yPage.locator('.job-card').first().waitFor();

  // Keyboard: ArrowRight across #jobsSegmentTabs moves focus and switches segment
  const firstTab = a11yPage.locator('#jobsSegmentTabs button[role="tab"]').first();
  await firstTab.focus();
  await a11yPage.keyboard.press('ArrowRight');
  const activeTabSeg = await a11yPage.evaluate(() => document.activeElement?.getAttribute('data-segment'));
  assert.equal(activeTabSeg, 'unverified_job_post', 'ArrowRight across #jobsSegmentTabs should move focus to second tab');
  await a11yPage.keyboard.press('ArrowLeft');
  assert.equal(await a11yPage.evaluate(() => document.activeElement?.getAttribute('data-segment')), 'verified_job');

  // Keyboard: ArrowDown selects first/next card and 'i' toggles #appInspector; stale selection resets on segment switch without erasing saved state
  await a11yPage.locator('.job-card').first().click();
  const prevUid = await a11yPage.evaluate(() => state.jobs.selectedUid);
  const prevId = await a11yPage.evaluate(() => state.jobs.selectedId);
  await a11yPage.locator('#insBody .job-row-bookmark').click();
  await a11yPage.locator('#jobsList').click({ position: { x: 4, y: 4 } }).catch(() => {});
  await a11yPage.keyboard.press('i');
  assert.equal(await a11yPage.locator('#appInspector').evaluate(el => el.classList.contains('collapsed')), true, 'Shortcut i should collapse #appInspector');
  await a11yPage.keyboard.press('i');
  assert.equal(await a11yPage.locator('#appInspector').evaluate(el => el.classList.contains('collapsed')), false, 'Shortcut i should reopen #appInspector');
  await a11yPage.keyboard.press('ArrowDown');
  const nextUid = await a11yPage.evaluate(() => state.jobs.selectedUid);
  assert.notEqual(nextUid, prevUid, 'ArrowDown should advance selected job');

  // Switching segment resets stale selectedUid/selectedId while preserving saved bookmark + showing neutral #insBody
  await a11yPage.locator('#jobsSegmentTabs button[data-segment="employer_lead"]').click();
  assert.equal(await a11yPage.evaluate(() => state.jobs.selectedUid), null, 'Switching segment should clear stale selectedUid');
  assert.equal(await a11yPage.evaluate(id => state.jobFavorites[id], prevId), true, 'Resetting selection must preserve saved jobFavorites');
  assert.ok(await a11yPage.locator('#insBody .jobs-inspector-empty').isVisible(), 'Open #appInspector with no selected item should show neutral empty prompt');
  await a11yCtx.close();

  // Mobile Esc restores focus to trigger card & touch targets >= 40px
  await phone.locator('#viewBtnList').click();
  const mobCloseCard = phone.locator('.job-card .job-card-open').first();
  await mobCloseCard.focus();
  await mobCloseCard.click();
  await phone.locator('#jobsDetailDrawer.open').waitFor();
  const minTouchHeight = await phone.locator('#jobsDrawerBody button, #jobsDrawerBody a.btn-primary').first().evaluate(el => el.getBoundingClientRect().height);
  assert.ok(minTouchHeight >= 40, `Expected mobile drawer CTA >= 40px, got ${minTouchHeight}`);
  await phone.keyboard.press('Escape');
  assert.equal(await phone.locator('#jobsDetailDrawer').evaluate(el => el.classList.contains('open')), false, 'Escape should close mobile drawer');
  const restoredFocus = await phone.evaluate(() => document.activeElement?.classList.contains('job-card-open'));
  assert.equal(restoredFocus, true, 'Closing mobile drawer with Esc should restore focus to trigger card');

  // Final engine checks: channel health strip, match + 462 inspector panels, prepare packet (no auto-submit), 88-day mode toggle
  assert.ok(await page.locator('#jobsChannelHealth').isVisible(), 'Expected channel health strip visible');
  assert.ok(await page.locator('#insBody').getByText(/Compatibilidade/).isVisible(), 'Expected match compatibility block in inspector');
  assert.ok(await page.locator('#insBody').getByText(/88 Dias \(WHV Subclass 462\)/).isVisible(), 'Expected 462 eligibility block in inspector');
  await page.locator('#insBody #btnPrepareApplication').click();
  await page.locator('#insBody #jobsPreparedPacketPanel').waitFor({ timeout: 5000 });
  assert.match((await page.locator('#insBody #jobsPreparedPacketPanel').textContent()) || '', /revisão manual/i);
  assert.ok((await page.locator('#insBody #prepDraftEn').inputValue()).length > 20);
  await page.locator('#btnJobs88Mode').click();
  await page.locator('#jobs88Banner').waitFor({ state: 'visible', timeout: 5000 });
  assert.match((await page.locator('#jobs88Banner').textContent()) || '', /home-affairs-462-2026-11/);
  await page.locator('#btnJobs88Mode').click();
  assert.equal(await page.locator('#jobs88Banner').isVisible(), false);

  await mobile.close();
  await desktop.close();
  assert.deepEqual(errors, []);
  console.log('Jobs UI: persisted API, evidence labels, saved state, mobile, final engine panels, and API failure passed.');
} finally {
  await browser.close();
}
