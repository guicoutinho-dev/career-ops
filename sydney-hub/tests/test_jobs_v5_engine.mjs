import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const engine = require('../web/jobs-v5-engine.js');

function makeFixtureRows(now = Date.now()) {
  const isoNow = new Date(now).toISOString();
  const isoOld = new Date(now - 20 * 86400_000).toISOString();
  const secNow = Math.floor(now / 1000);
  const secStale = secNow - 20 * 86400;

  return [
    {
      id: 'v5-a',
      title: 'Barista',
      company: 'Cafe One',
      suburb: 'Rockdale',
      location: 'Rockdale, NSW',
      source: 'Workable',
      source_url: 'https://a.example/1',
      verification_state: 'verified_job',
      published_at: isoNow,
      last_verified_at: isoNow,
      employment_type: 'Casual',
      requirements: 'RSA; White Card',
      pay_display: '$28/h',
      pay_hourly_min: 28,
      category: 'Hospitality',
      ats_provider: 'workable',
      role_bucket: 'barista_hospitality',
      discovery_source: 'Workable XML',
      commute_windows: { tuesday_0900: 18, friday_1800: 24 },
      commute_observations: [
        { departure_window: 'tuesday_0900', transit_minutes: 18, checked_at: secNow },
      ],
      evidence: [{ source_type: 'job_listing', source_url: 'https://a.example/1', supports_job: true, checked_at: secNow }],
      lat: -33.9525,
      lng: 151.1378,
    },
    {
      id: 'v5-b',
      title: 'Bartender',
      company: 'Pub Two',
      suburb: 'Mascot',
      location: 'Mascot, NSW',
      source: 'PageUp',
      source_url: 'https://b.example/2',
      verification_state: 'verified_job',
      published_at: isoOld,
      last_verified_at: isoNow,
      employment_type: 'Part-time',
      requirements: '["RCG"]',
      pay_display: '$42/h',
      pay_hourly_min: 42,
      category: 'Hospitality',
      ats_provider: 'pageup',
      role_bucket: 'barista_hospitality',
      discovery_source: 'OpenStreetMap discovery',
      commute_windows: { tuesday_0900: 52 },
      commute_observations: [
        { departure_window: 'tuesday_0900', transit_minutes: 52, checked_at: secStale },
      ],
      evidence: [{ source_type: 'job_listing', source_url: 'https://b.example/2', supports_job: true, checked_at: secNow }],
      lat: -33.9267,
      lng: 151.1879,
    },
    {
      id: 'v5-c',
      title: 'Kitchen Hand',
      company: 'Venue Three',
      suburb: 'Kogarah',
      location: 'Kogarah, NSW',
      source: 'Facebook Group',
      source_url: 'https://c.example/3',
      verification_state: 'unverified_job_post',
      published_at: null,
      last_verified_at: null,
      employment_type: null,
      requirements: null,
      pay_display: null,
      pay_hourly_min: null,
      category: 'Hospitality',
      ats_provider: null,
      role_bucket: 'kitchen_allrounder',
      commute_windows: {},
      evidence: [],
      lat: null,
      lng: null,
    },
    {
      id: 'v5-d',
      title: null,
      company: 'Rockdale Espresso Co',
      suburb: 'Rockdale',
      location: 'Rockdale, NSW',
      source: 'OpenStreetMap discovery',
      source_url: 'https://d.example/4',
      employer_website: 'https://d.example',
      verification_state: 'employer_lead',
      published_at: null,
      last_verified_at: null,
      employment_type: null,
      requirements: null,
      pay_display: null,
      pay_hourly_min: null,
      category: 'cafe',
      ats_provider: null,
      role_bucket: null,
      discovery_source: 'OpenStreetMap discovery',
      commute_windows: {},
      evidence: [],
      lat: -33.953,
      lng: 151.139,
    },
  ];
}

test('normalizeOpportunity parses real requirements without fabricating defaults on null fields', () => {
  const rows = makeFixtureRows().map((r, i) => engine.normalizeOpportunity(r, i));

  assert.deepEqual(rows[0].tickets_list, ['RSA', 'White Card']);
  assert.equal(rows[0].ticket_required, 'RSA; White Card');
  assert.deepEqual(rows[1].tickets_list, ['RCG']);
  assert.equal(rows[2].tickets_list, null);
  assert.equal(rows[2].ticket_required, null);
  assert.equal(rows[2].requirements, null);
  assert.equal(rows[2].visa_88_days, null);
  assert.equal(rows[3].displayTitle, 'Rockdale Espresso Co');
  assert.equal(rows[3].pay_hourly_min, null);
});

test('filterOpportunities enforces strict null exclusion by default and opts in with includeUnknown flags', () => {
  const rows = makeFixtureRows().map((r, i) => engine.normalizeOpportunity(r, i));
  const baseFilter = {
    segment: 'all',
    statusFilter: 'all',
    includeUnlocated: true,
    track: 'all',
    selectedSuburbs: [],
  };

  assert.deepEqual(
    engine.filterOpportunities(rows, { ...baseFilter, payMin: 30 }, { ignoreSegment: true }).map(x => x.id),
    ['v5-b']
  );
  assert.deepEqual(
    engine.filterOpportunities(rows, { ...baseFilter, payMin: 30, includeUnknownPay: true }, { ignoreSegment: true }).map(x => x.id),
    ['v5-b', 'v5-c', 'v5-d']
  );

  assert.deepEqual(
    engine.filterOpportunities(rows, { ...baseFilter, type: 'Casual' }, { ignoreSegment: true }).map(x => x.id),
    ['v5-a']
  );
  assert.deepEqual(
    engine.filterOpportunities(rows, { ...baseFilter, type: 'Casual', includeUnknownType: true }, { ignoreSegment: true }).map(x => x.id),
    ['v5-a', 'v5-c', 'v5-d']
  );

  assert.deepEqual(
    engine.filterOpportunities(rows, { ...baseFilter, req: 'RSA' }, { ignoreSegment: true }).map(x => x.id),
    ['v5-a']
  );
  assert.deepEqual(
    engine.filterOpportunities(rows, { ...baseFilter, req: 'RSA', includeUnknownReq: true }, { ignoreSegment: true }).map(x => x.id),
    ['v5-a', 'v5-c', 'v5-d']
  );

  assert.deepEqual(
    engine.filterOpportunities(rows, { ...baseFilter, recentDays: 7 }, { ignoreSegment: true }).map(x => x.id),
    ['v5-a']
  );
  assert.deepEqual(
    engine.filterOpportunities(rows, { ...baseFilter, recentDays: 7, includeUnknownRecent: true }, { ignoreSegment: true }).map(x => x.id),
    ['v5-a', 'v5-c', 'v5-d']
  );

  assert.deepEqual(
    engine.filterOpportunities(rows, { ...baseFilter, commuteWindow: 'tuesday_0900', maxCommuteMinutes: 45 }, { ignoreSegment: true }).map(x => x.id),
    ['v5-a']
  );
  assert.deepEqual(
    engine.filterOpportunities(rows, { ...baseFilter, commuteWindow: 'tuesday_0900', maxCommuteMinutes: 45, includeUnknownCommute: true }, { ignoreSegment: true }).map(x => x.id),
    ['v5-a', 'v5-c', 'v5-d']
  );
});

test('filterOpportunities and computeSegmentCounts separate the 3 verification states cleanly', () => {
  const rows = makeFixtureRows().map((r, i) => engine.normalizeOpportunity(r, i));
  const countsAll = engine.computeSegmentCounts(rows, { includeUnlocated: true, statusFilter: 'all' });
  assert.deepEqual(countsAll, { verified_job: 2, unverified_job_post: 1, employer_lead: 1, total: 4 });

  const countsRockdale = engine.computeSegmentCounts(rows, { includeUnlocated: true, statusFilter: 'all', selectedSuburbs: ['Rockdale'] });
  assert.deepEqual(countsRockdale, { verified_job: 1, unverified_job_post: 0, employer_lead: 1, total: 2 });

  assert.deepEqual(
    engine.filterOpportunities(rows, { segment: 'verified_job', includeUnlocated: true, statusFilter: 'all' }).map(x => x.id),
    ['v5-a', 'v5-b']
  );
  assert.deepEqual(
    engine.filterOpportunities(rows, { segment: 'employer_lead', includeUnlocated: true, statusFilter: 'all' }).map(x => x.id),
    ['v5-d']
  );
});

test('sortOpportunities pay_desc puts numeric pay before null salaries', () => {
  const rows = makeFixtureRows().map((r, i) => engine.normalizeOpportunity(r, i));
  const sorted = engine.sortOpportunities(rows, 'pay_desc');
  assert.deepEqual(sorted.map(x => x.id), ['v5-b', 'v5-a', 'v5-c', 'v5-d']);
});

test('buildJobDetailModel enforces honest unknown states, ODbL attribution, stale commute, and prospecting CTA', () => {
  const rows = makeFixtureRows().map((r, i) => engine.normalizeOpportunity(r, i));

  const verifiedDetail = engine.buildJobDetailModel(rows[0], { userStatus: {}, userFavorites: {}, userNotes: {} });
  assert.equal(verifiedDetail.verification.glyph, 'shield-check');
  assert.equal(verifiedDetail.verification.label, 'Vaga verificada');
  assert.equal(verifiedDetail.ctaMode, 'apply');
  assert.equal(verifiedDetail.needsOsmAttribution, false);
  assert.equal(verifiedDetail.payText, '$28/h');
  assert.equal(verifiedDetail.commuteStale, false);

  const osmWithAtsDetail = engine.buildJobDetailModel(rows[1], { userStatus: {}, userFavorites: {}, userNotes: {} });
  assert.equal(osmWithAtsDetail.needsOsmAttribution, true);
  assert.equal(osmWithAtsDetail.commuteStale, true);

  const communityDetail = engine.buildJobDetailModel(rows[2], { userStatus: {}, userFavorites: {}, userNotes: {} });
  assert.equal(communityDetail.verification.glyph, 'message-dashed');
  assert.equal(communityDetail.payText, 'Não informado');
  assert.equal(communityDetail.commuteText, 'Trajeto não verificado');
  assert.equal(communityDetail.employmentTypeText, 'Não informado');

  const leadDetail = engine.buildJobDetailModel(rows[3], { userStatus: {}, userFavorites: {}, userNotes: {} });
  assert.equal(leadDetail.verification.glyph, 'building-lead');
  assert.equal(leadDetail.verification.label, 'Empresa para prospecção');
  assert.equal(leadDetail.ctaMode, 'prospect');
  assert.equal(leadDetail.needsOsmAttribution, true);
});

test('ROLE_BUCKET_LABELS maps short backend role_bucket tokens cleanly', () => {
  for (const token of [
    'barista',
    'bartender',
    'kitchen',
    'front_of_house',
    'hospitality',
    'cleaner',
    'housekeeping',
    'retail',
    'warehouse',
  ]) {
    const normalized = engine.normalizeOpportunity(
      {
        id: `tok-${token}`,
        title: 'Test Role',
        company: 'Test Co',
        verification_state: 'verified_job',
        role_bucket: token,
        evidence: [{ source_type: 'job_listing', supports_job: true, checked_at: 1000 }],
      },
      0,
    );
    const detail = engine.buildJobDetailModel(normalized, { userStatus: {}, userFavorites: {}, userNotes: {} });
    assert.ok(detail.roleBucketLabel && detail.roleBucketLabel !== token, `expected mapped label for ${token}`);
  }
});

test('final engine 88-day mode, lifecycle states, and match normalization work deterministically', () => {
  assert.ok(engine.SPECIFIED_WORK_META && engine.SPECIFIED_WORK_META.eligible);
  assert.ok(engine.LIFECYCLE_META && engine.LIFECYCLE_META.expired && engine.LIFECYCLE_META.stale);

  const items = [
    {
      id: 'syd-1',
      title: 'Barista',
      company: 'Rockdale Cafe',
      suburb: 'Rockdale',
      verification_state: 'verified_job',
      lifecycle_state: 'active',
      match: { score: 84, tier: 'strong', reasons: ['Cargo alvo'], unknowns: [], suspicious_signals: [] },
      specified_work_462: {
        status: 'ineligible',
        rules_version: 'home-affairs-462-2026-11',
        reasons: ['Sydney metro não elegível para hospitality 462'],
        missing_facts: [],
        disclaimer: 'Orientativo — confirme com Home Affairs',
      },
      evidence: [{ source_type: 'job_listing', supports_job: true, checked_at: 1000 }],
    },
    {
      id: 'reg-2',
      title: 'Hospitality All-Rounder',
      company: 'Reef Resort',
      suburb: 'Cairns',
      verification_state: 'verified_job',
      lifecycle_state: 'stale',
      is_stale: true,
      match: { score: 78, tier: 'good', reasons: ['WHV 462'], unknowns: ['Salário não informado'], suspicious_signals: [] },
      specified_work_462: {
        status: 'eligible',
        rules_version: 'home-affairs-462-2026-11',
        reasons: ['Cairns QLD 4870 / Northern Australia'],
        missing_facts: [],
        disclaimer: 'Orientativo — confirme com Home Affairs',
      },
      evidence: [{ source_type: 'job_listing', supports_job: true, checked_at: 1000 }],
    },
    {
      id: 'exp-3',
      title: 'Cleaner',
      company: 'Old Co',
      suburb: 'Mascot',
      verification_state: 'verified_job',
      lifecycle_state: 'expired',
      match: { score: 50, tier: 'low', reasons: [], unknowns: [], suspicious_signals: ['Expirada'] },
      specified_work_462: {
        status: 'needs_review',
        rules_version: 'home-affairs-462-2026-11',
        reasons: [],
        missing_facts: ['Postcode não informado'],
        disclaimer: 'Orientativo — confirme com Home Affairs',
      },
      evidence: [{ source_type: 'job_listing', supports_job: true, checked_at: 1000 }],
    },
  ].map((row, idx) => engine.normalizeOpportunity(row, idx));

  const view88 = engine.filterOpportunities(
    items,
    { view: '88_days', hideExpired: true, includeUnlocated: true, statusFilter: 'active' },
    { ignoreSegment: true }
  );
  assert.deepEqual(view88.map(x => x.id), ['reg-2']);

  const byMatch = engine.sortOpportunities(items, 'match_desc');
  assert.deepEqual(byMatch.map(x => x.id), ['syd-1', 'reg-2', 'exp-3']);

  const regDetail = engine.buildJobDetailModel(items[1], {});
  assert.equal(regDetail.isStale, true);
  assert.equal(regDetail.isExpired, false);
  assert.equal(regDetail.specifiedWork462.status, 'eligible');
  assert.equal(regDetail.match.score, 78);

  const expDetail = engine.buildJobDetailModel(items[2], {});
  assert.equal(expDetail.isExpired, true);
  assert.equal(expDetail.lifecycleMeta.label, 'Expirada');
});
