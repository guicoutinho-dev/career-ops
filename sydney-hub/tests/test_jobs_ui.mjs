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
  assert.equal(await page.locator('.job-card').count(), 2);
  assert.equal(await page.locator('#jobsSuburbFilter option[value="Rockdale"]').count(), 1);
  assert.equal(await page.locator('#jobFacetFilters button[data-filter="region"][data-value="NSW"]').count(), 1);
  assert.equal(await page.locator('.job-card[data-verification="employer_lead"]').count(), 1);
  assert.equal(await page.locator('.job-card[data-verification="unverified_job_post"]').count(), 1);
  assert.equal(await page.getByText('$99/h').count(), 0);
  await page.evaluate(() => openRockdaleReportPanel());
  assert.equal(await page.locator('#rockdaleReportContent .report-item-card').count(), 1);
  assert.ok(await page.locator('#rockdaleReportContent').getByText('Anúncio não verificado').isVisible());
  assert.equal(await page.locator('#rockdaleReportContent').getByText('$99/h').count(), 0);
  await page.evaluate(() => closeRockdaleReportModal());
  assert.equal(await page.locator('#jobsView').evaluate(el => el.classList.contains('list-only')), false);
  await page.locator('#jobsMapLoading').waitFor({ state: 'hidden', timeout: 20000 });

  await page.locator('.job-card[data-verification="unverified_job_post"] .job-card-open').click();
  assert.ok(await page.locator('#jobsDetailDrawer').evaluate(el => el.classList.contains('open')));
  assert.equal(await page.locator('#jobsDrawerBody textarea').inputValue(), 'Contato anterior');
  assert.ok(await page.locator('#jobsDrawerBody .job-row-bookmark').evaluate(el => el.classList.contains('active')));
  await page.locator('#jobsDrawerBody textarea').fill('Nota preservada');
  await page.getByRole('button', { name: 'Registrar candidatura' }).click();
  assert.equal(await page.evaluate(() => state.jobStatus['legacy-barista']), 'applied');
  assert.equal(await page.evaluate(() => state.jobFavorites['legacy-barista']), true);
  await page.reload();
  await page.locator('.job-card').first().waitFor();
  assert.equal(await page.evaluate(() => state.jobStatus['legacy-barista']), 'applied');
  assert.equal(await page.evaluate(() => state.jobFavorites['legacy-barista']), true);
  await page.locator('.job-card[data-verification="unverified_job_post"] .job-card-open').click();
  assert.equal(await page.locator('#jobsDrawerBody textarea').inputValue(), 'Nota preservada');
  await page.locator('#jobsDetailDrawer button[title^="Fechar detalhes"]').click();

  await page.locator('#jobsSearchInput').fill('Barista');
  page.once('dialog', dialog => dialog.accept('Busca de teste'));
  await page.getByRole('button', { name: 'Salvar busca' }).click();
  await page.reload();
  await page.locator('.job-card').first().waitFor();
  assert.ok(await page.getByText('Busca de teste').count() > 0);
  await page.locator('#jobsSearchInput').fill('');
  await page.locator('.job-card[data-verification="employer_lead"] .job-card-open').click();
  assert.ok(await page.getByText('Empresa para prospecção · sem vaga confirmada').isVisible());
  assert.equal(await page.locator('#jobsDrawerActions a').getAttribute('href'), 'https://example.com/hotel');
  await page.getByRole('button', { name: 'Registrar contato' }).click();
  assert.equal(await page.evaluate(() => state.jobStatus['legacy-lead']), 'contacted');
  assert.equal(await page.evaluate(() => { discardJob(null, 'legacy-lead'); restoreLastDiscarded(); return state.jobStatus['legacy-lead']; }), 'contacted');
  await page.locator('#wsBtnStays').click();
  assert.ok(await page.locator('#staysView').isVisible());

  const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true });
  const phone = await mobile.newPage();
  phone.on('pageerror', error => errors.push(error.message));
  await phone.goto(baseUrl);
  await phone.locator('.job-card').first().waitFor();
  assert.equal(await phone.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  assert.ok(await phone.locator('#jobsView').evaluate(el => el.classList.contains('list-only')));
  await phone.locator('#viewBtnSplit').click();
  assert.equal(await phone.locator('#jobsMapPane').evaluate(el => getComputedStyle(el).display), 'block');
  await phone.locator('.map-pill-marker').first().waitFor();

  const failed = await browser.newContext();
  const offline = await failed.newPage();
  await offline.route('**/api/opportunities', route => route.fulfill({ status: 503, body: 'unavailable' }));
  await offline.goto(baseUrl);
  assert.ok(await offline.getByText('Catálogo indisponível').isVisible());
  assert.equal(await offline.locator('.job-card').count(), 0);
  await offline.unroute('**/api/opportunities');
  await offline.getByRole('button', { name: 'Tentar novamente' }).click();
  await offline.locator('.job-card').first().waitFor();
  assert.equal(await offline.locator('.job-card').count(), 2);
  assert.deepEqual(errors, []);
  await failed.close();
  await mobile.close();
  await desktop.close();
  console.log('Jobs UI: persisted API, evidence labels, saved state, mobile, and API failure passed.');
} finally {
  await browser.close();
}
