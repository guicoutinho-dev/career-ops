(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.SydneyJobsV5 = api;
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const STALE_COMMUTE_SECONDS = 14 * 86400;

  const VERIFICATION_META = Object.freeze({
    verified_job: Object.freeze({
      state: 'verified_job',
      label: 'Vaga verificada',
      shortLabel: 'Verificada',
      glyph: 'shield-check',
      ctaMode: 'apply',
      ctaLabel: 'Candidatar-se',
    }),
    unverified_job_post: Object.freeze({
      state: 'unverified_job_post',
      label: 'Anúncio comunitário',
      shortLabel: 'Comunitário',
      glyph: 'message-dashed',
      ctaMode: 'open',
      ctaLabel: 'Abrir anúncio',
    }),
    employer_lead: Object.freeze({
      state: 'employer_lead',
      label: 'Empresa para prospecção',
      shortLabel: 'Empresa',
      glyph: 'building-lead',
      ctaMode: 'prospect',
      ctaLabel: 'Abrir site / carreira',
    }),
  });

  const SPECIFIED_WORK_META = Object.freeze({
    eligible: Object.freeze({
      status: 'eligible',
      label: 'Elegível (WHV 462)',
      shortLabel: '88d Elegível',
    }),
    needs_review: Object.freeze({
      status: 'needs_review',
      label: 'Requer revisão manual',
      shortLabel: '88d Revisar',
    }),
    ineligible: Object.freeze({
      status: 'ineligible',
      label: 'Não elegível (WHV 462)',
      shortLabel: 'Fora 88d',
    }),
  });

  const LIFECYCLE_META = Object.freeze({
    active: Object.freeze({
      state: 'active',
      label: 'Ativa',
    }),
    stale: Object.freeze({
      state: 'stale',
      label: 'Evidência antiga (>7d)',
    }),
    expired: Object.freeze({
      state: 'expired',
      label: 'Expirada',
    }),
  });

  const ROLE_BUCKET_LABELS = Object.freeze({
    barista_hospitality: 'Barista / Bartender / Bar',
    barista: 'Barista',
    bartender: 'Bartender / Bar',
    kitchen_allrounder: 'Kitchen / Waitstaff / All-Rounder',
    kitchen: 'Kitchen / Cook',
    front_of_house: 'Front of House / Waitstaff',
    hospitality: 'Hospitality',
    cleaner_housekeeping: 'Cleaner / Housekeeping',
    cleaner: 'Cleaner',
    housekeeping: 'Housekeeping',
    retail_assistant: 'Retail Assistant',
    retail: 'Retail',
    warehouse_logistics: 'Warehouse Picker / Packer',
    warehouse: 'Warehouse / Logistics',
  });

  function cleanString(value) {
    if (value === null || value === undefined) return null;
    const trimmed = String(value).trim();
    return trimmed.length > 0 ? trimmed : null;
  }

  function parseRequirementsList(raw) {
    if (raw === null || raw === undefined) return null;
    if (Array.isArray(raw)) {
      const cleaned = raw.map(cleanString).filter(Boolean);
      return cleaned.length > 0 ? cleaned : null;
    }
    const text = cleanString(raw);
    if (!text) return null;
    if (text.startsWith('[')) {
      try {
        const parsed = JSON.parse(text);
        if (Array.isArray(parsed)) {
          const cleaned = parsed.map(cleanString).filter(Boolean);
          return cleaned.length > 0 ? cleaned : null;
        }
      } catch (_) {
        // Fallback to delimiter split when string is not valid JSON.
      }
    }
    const parts = text.split(/[;,|]/).map(cleanString).filter(Boolean);
    return parts.length > 0 ? parts : null;
  }

  function deriveTrack(rawJob) {
    if (rawJob && cleanString(rawJob.track)) {
      return String(rawJob.track).trim();
    }
    if (rawJob && rawJob.visa_88_days === true) {
      return '88 Days Regional';
    }
    const text = [
      rawJob && rawJob.category,
      rawJob && rawJob.title,
      rawJob && rawJob.role_bucket,
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase();
    if (/\bsap\b|\babap\b|\bbtp\b|\bs\/4hana\b/.test(text)) {
      return 'SAP / Tech';
    }
    return 'Sydney Immediate';
  }

  function normalizeVerificationState(rawState) {
    if (rawState && Object.prototype.hasOwnProperty.call(VERIFICATION_META, rawState)) {
      return rawState;
    }
    return 'unverified_job_post';
  }

  function normalizeOpportunity(rawJob, idx = 0, classifyGeoFn = null) {
    const job = rawJob || {};
    const verificationState = normalizeVerificationState(job.verification_state);
    const company = cleanString(job.company) || 'Empregador não identificado';
    const rawTitle = cleanString(job.title);
    const displayTitle =
      verificationState === 'employer_lead'
        ? rawTitle || company
        : rawTitle || company || 'Oportunidade sem título';

    const rawRequirements =
      job.requirements !== undefined
        ? job.requirements
        : job.tickets_list !== undefined
          ? job.tickets_list
          : job.ticket_required;
    const ticketsList = parseRequirementsList(rawRequirements);
    const ticketRequired = ticketsList ? ticketsList.join('; ') : null;

    const payMin =
      typeof job.pay_hourly_min === 'number' && Number.isFinite(job.pay_hourly_min)
        ? job.pay_hourly_min
        : null;
    const payDisplay = cleanString(job.pay_display);

    const commuteWindows =
      job.commute_windows && typeof job.commute_windows === 'object' && !Array.isArray(job.commute_windows)
        ? { ...job.commute_windows }
        : {};
    const commuteObservations = Array.isArray(job.commute_observations)
      ? job.commute_observations.map(obs => ({ ...obs }))
      : [];

    const normalized = {
      ...job,
      id: job.id || `opp_${idx}`,
      verification_state: verificationState,
      company,
      title: rawTitle,
      displayTitle,
      suburb: cleanString(job.suburb),
      location: cleanString(job.location),
      state: cleanString(job.state),
      source: cleanString(job.source) || 'Desconhecida',
      source_url: cleanString(job.source_url || job.url),
      url: cleanString(job.url || job.source_url),
      employer_website: cleanString(job.employer_website),
      discovery_source: cleanString(job.discovery_source),
      ats_provider: cleanString(job.ats_provider),
      role_bucket: cleanString(job.role_bucket),
      category: cleanString(job.category),
      employment_type: cleanString(job.employment_type),
      experience_required: cleanString(job.experience_required),
      requirements: job.requirements !== undefined && job.requirements !== null ? job.requirements : null,
      tickets_list: ticketsList,
      ticket_required: ticketRequired,
      pay_hourly_min: payMin,
      pay_display: payDisplay,
      visa_88_days: typeof job.visa_88_days === 'boolean' ? job.visa_88_days : null,
      published_at: cleanString(job.published_at),
      posted_date: cleanString(job.published_at || job.posted_date),
      last_verified_at: cleanString(job.last_verified_at),
      commute_windows: commuteWindows,
      commute_observations: commuteObservations,
      commute_unknown:
        typeof job.commute_unknown === 'boolean'
          ? job.commute_unknown
          : Object.keys(commuteWindows).length === 0,
      track: deriveTrack(job),
      evidence: Array.isArray(job.evidence) ? job.evidence.map(ev => ({ ...ev })) : [],
      lifecycle_state: LIFECYCLE_META[job.lifecycle_state] ? job.lifecycle_state : 'active',
      stale_since: cleanString(job.stale_since),
      expired_at: cleanString(job.expired_at),
      is_stale: Boolean(job.is_stale || job.lifecycle_state === 'stale'),
      match:
        job.match && typeof job.match === 'object'
          ? {
              score: typeof job.match.score === 'number' && Number.isFinite(job.match.score) ? job.match.score : 0,
              tier: cleanString(job.match.tier) || 'low',
              matched_role_bucket: cleanString(job.match.matched_role_bucket),
              reasons: Array.isArray(job.match.reasons) ? job.match.reasons.slice() : [],
              unknowns: Array.isArray(job.match.unknowns) ? job.match.unknowns.slice() : [],
              suspicious_signals: Array.isArray(job.match.suspicious_signals) ? job.match.suspicious_signals.slice() : [],
              unresolved_fields: Array.isArray(job.match.unresolved_fields) ? job.match.unresolved_fields.slice() : [],
            }
          : null,
      specified_work_462:
        job.specified_work_462 && typeof job.specified_work_462 === 'object'
          ? {
              status: SPECIFIED_WORK_META[job.specified_work_462.status] ? job.specified_work_462.status : 'needs_review',
              rules_version: cleanString(job.specified_work_462.rules_version) || 'home-affairs-462-2026-11',
              matched_industry: cleanString(job.specified_work_462.matched_industry),
              postcode: cleanString(job.specified_work_462.postcode),
              region_eligible: typeof job.specified_work_462.region_eligible === 'boolean' ? job.specified_work_462.region_eligible : null,
              reasons: Array.isArray(job.specified_work_462.reasons) ? job.specified_work_462.reasons.slice() : [],
              missing_facts: Array.isArray(job.specified_work_462.missing_facts) ? job.specified_work_462.missing_facts.slice() : [],
              disclaimer: cleanString(job.specified_work_462.disclaimer) || 'Regras orientativas de trabalho especificado Subclass 462 — confirme sempre no Home Affairs.',
            }
          : {
              status: 'needs_review',
              rules_version: 'home-affairs-462-2026-11',
              matched_industry: null,
              postcode: null,
              region_eligible: null,
              reasons: [],
              missing_facts: ['Indústria e postcode não verificados automaticamente para Subclass 462.'],
              disclaimer: 'Regras orientativas de trabalho especificado Subclass 462 — confirme sempre no Home Affairs.',
            },
    };

    if (typeof classifyGeoFn === 'function') {
      return classifyGeoFn(normalized, idx);
    }

    const hasNumericCoords =
      typeof normalized.lat === 'number' &&
      Number.isFinite(normalized.lat) &&
      typeof normalized.lng === 'number' &&
      Number.isFinite(normalized.lng);

    if (normalized.locationPrecision === undefined) {
      normalized.locationPrecision = hasNumericCoords ? 'exact' : normalized.suburb ? 'suburb' : 'unknown';
    }
    return normalized;
  }

  function resolveEffectiveStatus(filterState) {
    const saved = filterState && filterState.savedFilter;
    if (saved === 'discarded') return 'discarded';
    if (saved === 'shortlist') return 'shortlist';
    return (filterState && filterState.statusFilter) || 'active';
  }

  function resolvePayBounds(j) {
    const rawMin = j.payMin !== undefined ? j.payMin : j.salaryMin;
    const rawMax = j.payMax !== undefined ? j.payMax : j.salaryMax;
    const payMin = rawMin !== null && rawMin !== undefined && rawMin !== '' && Number.isFinite(Number(rawMin))
      ? Number(rawMin)
      : null;
    const payMax = rawMax !== null && rawMax !== undefined && rawMax !== '' && Number.isFinite(Number(rawMax))
      ? Number(rawMax)
      : null;
    return { payMin, payMax };
  }

  function filterOpportunities(allItems, filterState = {}, deps = {}) {
    const j = filterState || {};
    const userStatus = deps.userStatus || {};
    const userFavorites = deps.userFavorites || {};
    const nowMs = typeof deps.nowMs === 'number' ? deps.nowMs : Date.now();
    const ignoreBounds = Boolean(deps.ignoreBounds);
    const ignoreSegment = Boolean(deps.ignoreSegment);

    let items = Array.isArray(allItems) ? allItems.slice() : [];

    if (j.activeCollection && Array.isArray(j.collections)) {
      const col = j.collections.find(c => c.id === j.activeCollection);
      if (col && Array.isArray(col.jobIds)) {
        const idSet = new Set(col.jobIds);
        items = items.filter(x => idSet.has(x.id));
      }
    }

    const effectiveStatus = resolveEffectiveStatus(j);
    if (effectiveStatus === 'discarded') {
      items = items.filter(x => userStatus[x.id] === 'discarded');
    } else if (effectiveStatus === 'shortlist') {
      items = items.filter(x => Boolean(userFavorites[x.id]));
    } else if (effectiveStatus === 'applied') {
      items = items.filter(x => userStatus[x.id] === 'applied');
    } else if (effectiveStatus === 'active') {
      items = items.filter(x => userStatus[x.id] !== 'discarded');
    }

    if (!ignoreSegment && j.segment && j.segment !== 'all') {
      items = items.filter(x => x.verification_state === j.segment);
    }

    if (j.hideExpired) {
      items = items.filter(x => x.lifecycle_state !== 'expired');
    }

    if (j.view === '88_days') {
      items = items.filter(
        x => x.specified_work_462 && (x.specified_work_462.status === 'eligible' || x.specified_work_462.status === 'needs_review')
      );
    }

    if (j.specifiedWorkStatus && j.specifiedWorkStatus !== 'all') {
      items = items.filter(x => x.specified_work_462 && x.specified_work_462.status === j.specifiedWorkStatus);
    }

    if (j.track && j.track !== 'all') {
      if (j.track === 'immediate') {
        items = items.filter(x => x.track === 'Sydney Immediate');
      } else if (j.track === 'regional') {
        items = items.filter(x => x.track === '88 Days Regional' || x.visa_88_days === true);
      } else if (j.track === 'sap') {
        items = items.filter(x => x.track === 'SAP / Tech' || (x.category || '').toUpperCase().includes('SAP'));
      }
    }

    if (Array.isArray(j.selectedSuburbs) && j.selectedSuburbs.length > 0) {
      const targets = j.selectedSuburbs.map(s => String(s).trim().toLowerCase()).filter(Boolean);
      if (targets.length > 0) {
        items = items.filter(x => {
          const sub = (x.suburb || '').toLowerCase();
          const loc = (x.location || '').toLowerCase();
          return targets.some(t => sub.includes(t) || loc.includes(t));
        });
      }
    }

    if (j.roleBucket && j.roleBucket !== 'all') {
      items = items.filter(x => x.role_bucket === j.roleBucket);
    }

    if (j.category && j.category !== 'all') {
      const cat = String(j.category).trim().toLowerCase();
      items = items.filter(x => (x.category || '').toLowerCase() === cat);
    }

    if (j.source && j.source !== 'all') {
      const src = String(j.source).trim().toLowerCase();
      items = items.filter(x => (x.source || '').toLowerCase() === src);
    }

    if (j.atsProvider && j.atsProvider !== 'all') {
      const ats = String(j.atsProvider).trim().toLowerCase();
      items = items.filter(x => (x.ats_provider || '').toLowerCase() === ats);
    }

    if (j.region) {
      items = items.filter(x => x.state === j.region);
    }

    if (j.type && j.type !== 'all') {
      const targetType = String(j.type).trim().toLowerCase();
      const includeUnknownType = Boolean(j.includeUnknownType);
      items = items.filter(x => {
        if (!x.employment_type) return includeUnknownType;
        return x.employment_type.toLowerCase() === targetType;
      });
    }

    if (j.req && j.req !== 'all') {
      const targetReq = String(j.req).trim().toLowerCase();
      const includeUnknownReq = Boolean(j.includeUnknownReq);
      items = items.filter(x => {
        const hasKnownReqs = Array.isArray(x.tickets_list) && x.tickets_list.length > 0;
        if (!hasKnownReqs) return includeUnknownReq;
        return (
          x.tickets_list.some(t => t.toLowerCase().includes(targetReq)) ||
          (x.ticket_required || '').toLowerCase().includes(targetReq)
        );
      });
    }

    const { payMin, payMax } = resolvePayBounds(j);
    if (payMin !== null || payMax !== null) {
      const includeUnknownPay = Boolean(j.includeUnknownPay);
      items = items.filter(x => {
        const p = x.pay_hourly_min;
        if (typeof p !== 'number' || !Number.isFinite(p)) {
          return includeUnknownPay;
        }
        if (payMin !== null && p < payMin) return false;
        if (payMax !== null && p > payMax) return false;
        return true;
      });
    }

    const rawRecentDays = j.recentDays !== undefined ? j.recentDays : j.ageDays;
    const recentDays =
      rawRecentDays !== null && rawRecentDays !== undefined && rawRecentDays !== '' && Number.isFinite(Number(rawRecentDays))
        ? Number(rawRecentDays)
        : null;
    if (recentDays !== null && recentDays > 0) {
      const includeUnknownRecent = Boolean(j.includeUnknownRecent);
      const cutoffMs = nowMs - recentDays * 86400_000;
      items = items.filter(x => {
        const rawDate = x.published_at || x.posted_date;
        if (!rawDate) return includeUnknownRecent;
        const parsed = Date.parse(rawDate);
        if (!Number.isFinite(parsed)) return includeUnknownRecent;
        return parsed >= cutoffMs && parsed <= nowMs + 60_000;
      });
    }

    const maxCommute =
      j.maxCommuteMinutes !== null &&
      j.maxCommuteMinutes !== undefined &&
      j.maxCommuteMinutes !== '' &&
      Number.isFinite(Number(j.maxCommuteMinutes))
        ? Number(j.maxCommuteMinutes)
        : null;
    if (maxCommute !== null && maxCommute >= 0) {
      const win = j.commuteWindow || 'tuesday_0900';
      const includeUnknownCommute = Boolean(j.includeUnknownCommute);
      items = items.filter(x => {
        const mins = x.commute_windows && x.commute_windows[win];
        if (typeof mins !== 'number' || !Number.isFinite(mins)) {
          return includeUnknownCommute;
        }
        return mins <= maxCommute;
      });
    }

    if (j.visa88Only) items = items.filter(x => x.visa_88_days === true);
    if (j.immediateOnly) items = items.filter(x => x.immediate_start === true);
    if (j.whvOnly) items = items.filter(x => x.whv_eligible === true);

    if (j.searchQuery && String(j.searchQuery).trim()) {
      const q = String(j.searchQuery).toLowerCase().trim();
      items = items.filter(x =>
        (x.displayTitle || '').toLowerCase().includes(q) ||
        (x.title || '').toLowerCase().includes(q) ||
        (x.company || '').toLowerCase().includes(q) ||
        (x.suburb || '').toLowerCase().includes(q) ||
        (x.location || '').toLowerCase().includes(q) ||
        (x.category || '').toLowerCase().includes(q) ||
        (x.ats_provider || '').toLowerCase().includes(q) ||
        (x.source || '').toLowerCase().includes(q) ||
        (x.employment_type || '').toLowerCase().includes(q) ||
        (x.ticket_required || '').toLowerCase().includes(q) ||
        (x.description || '').toLowerCase().includes(q)
      );
    }

    if (j.includeUnlocated === false) {
      items = items.filter(x => x.locationPrecision !== 'unknown');
    }

    if (!ignoreBounds && j.mapBounds) {
      const b = j.mapBounds;
      items = items.filter(x => {
        if (x.locationPrecision === 'unknown' || x.lat === null || x.lng === null) return false;
        return x.lat >= b.south && x.lat <= b.north && x.lng >= b.west && x.lng <= b.east;
      });
    }

    return items;
  }

  function computeSegmentCounts(allItems, filterState = {}, deps = {}) {
    const items = filterOpportunities(allItems, filterState, { ...deps, ignoreSegment: true });
    const counts = {
      verified_job: 0,
      unverified_job_post: 0,
      employer_lead: 0,
      total: 0,
    };
    for (const item of items) {
      if (item.verification_state === 'verified_job') counts.verified_job += 1;
      else if (item.verification_state === 'employer_lead') counts.employer_lead += 1;
      else counts.unverified_job_post += 1;
    }
    counts.total = counts.verified_job + counts.unverified_job_post + counts.employer_lead;
    return counts;
  }

  function sortOpportunities(items, sortBy = 'newest', options = {}) {
    const copy = Array.isArray(items) ? items.slice() : [];
    const commuteWindow = options.commuteWindow || 'tuesday_0900';

    if (sortBy === 'match_desc') {
      return copy.sort((a, b) => {
        const aScore = a.match && typeof a.match.score === 'number' ? a.match.score : -1;
        const bScore = b.match && typeof b.match.score === 'number' ? b.match.score : -1;
        if (bScore !== aScore) return bScore - aScore;
        return String(a.id).localeCompare(String(b.id));
      });
    }

    if (sortBy === '88_days') {
      const rank = { eligible: 0, needs_review: 1, ineligible: 2 };
      return copy.sort((a, b) => {
        const ra = rank[a.specified_work_462 && a.specified_work_462.status] ?? 2;
        const rb = rank[b.specified_work_462 && b.specified_work_462.status] ?? 2;
        if (ra !== rb) return ra - rb;
        const aScore = a.match && typeof a.match.score === 'number' ? a.match.score : -1;
        const bScore = b.match && typeof b.match.score === 'number' ? b.match.score : -1;
        if (bScore !== aScore) return bScore - aScore;
        return String(a.id).localeCompare(String(b.id));
      });
    }

    if (sortBy === 'pay_desc') {
      return copy.sort((a, b) => {
        const aVal = typeof a.pay_hourly_min === 'number' && Number.isFinite(a.pay_hourly_min) ? a.pay_hourly_min : null;
        const bVal = typeof b.pay_hourly_min === 'number' && Number.isFinite(b.pay_hourly_min) ? b.pay_hourly_min : null;
        if (aVal !== null && bVal !== null) {
          if (bVal !== aVal) return bVal - aVal;
        } else if (aVal !== null) {
          return -1;
        } else if (bVal !== null) {
          return 1;
        }
        return (b.first_seen_at || 0) - (a.first_seen_at || 0) || String(a.id).localeCompare(String(b.id));
      });
    }

    if (sortBy === 'commute_asc') {
      return copy.sort((a, b) => {
        const aMin = a.commute_windows && typeof a.commute_windows[commuteWindow] === 'number'
          ? a.commute_windows[commuteWindow]
          : null;
        const bMin = b.commute_windows && typeof b.commute_windows[commuteWindow] === 'number'
          ? b.commute_windows[commuteWindow]
          : null;
        if (aMin !== null && bMin !== null) {
          if (aMin !== bMin) return aMin - bMin;
        } else if (aMin !== null) {
          return -1;
        } else if (bMin !== null) {
          return 1;
        }
        return String(a.id).localeCompare(String(b.id));
      });
    }

    if (sortBy === 'company') {
      return copy.sort((a, b) => (a.company || '').localeCompare(b.company || ''));
    }

    if (sortBy === 'suburb') {
      return copy.sort((a, b) => (a.suburb || a.location || '').localeCompare(b.suburb || b.location || ''));
    }

    return copy.sort((a, b) => {
      const aTime = a.published_at ? Date.parse(a.published_at) : NaN;
      const bTime = b.published_at ? Date.parse(b.published_at) : NaN;
      const aOk = Number.isFinite(aTime);
      const bOk = Number.isFinite(bTime);
      if (aOk && bOk && bTime !== aTime) return bTime - aTime;
      if (aOk && !bOk) return -1;
      if (!aOk && bOk) return 1;
      return (b.first_seen_at || 0) - (a.first_seen_at || 0) || String(a.id).localeCompare(String(b.id));
    });
  }

  function formatDateDisplay(isoOrDate) {
    if (!isoOrDate) return null;
    const ts = Date.parse(isoOrDate);
    if (!Number.isFinite(ts)) return cleanString(isoOrDate);
    try {
      return new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short' }).format(new Date(ts));
    } catch (_) {
      return new Date(ts).toISOString().slice(0, 10);
    }
  }

  function formatCommuteSummary(job, preferredWindow) {
    const windows = job && job.commute_windows ? job.commute_windows : {};
    const entries = Object.entries(windows).filter(([, m]) => typeof m === 'number' && Number.isFinite(m));
    if (entries.length === 0) return 'Trajeto não verificado';
    const order = preferredWindow && typeof windows[preferredWindow] === 'number'
      ? [[preferredWindow, windows[preferredWindow]], ...entries.filter(([k]) => k !== preferredWindow)]
      : entries;
    const labels = {
      tuesday_0900: 'Ter 09:00',
      friday_1800: 'Sex 18:00',
    };
    return order
      .map(([win, mins]) => `${labels[win] || win}: ${mins} min`)
      .join(' · ');
  }

  function isCommuteStale(job, nowSec) {
    const observations = Array.isArray(job && job.commute_observations) ? job.commute_observations : [];
    const timestamps = observations
      .map(o => Number(o && o.checked_at))
      .filter(n => Number.isFinite(n) && n > 0);
    if (timestamps.length === 0) return false;
    const newest = Math.max(...timestamps);
    return (nowSec - newest) > STALE_COMMUTE_SECONDS;
  }

  function buildJobDetailModel(job, context = {}) {
    const raw = job || {};
    const verificationState = normalizeVerificationState(raw.verification_state);
    const verification = VERIFICATION_META[verificationState];
    const nowSec = typeof context.nowSec === 'number' ? context.nowSec : Math.floor(Date.now() / 1000);
    const userFavorites = context.userFavorites || {};
    const userStatus = context.userStatus || {};
    const userNotes = context.userNotes || {};

    const title =
      verificationState === 'employer_lead'
        ? (cleanString(raw.title) || cleanString(raw.company) || 'Empresa para prospecção')
        : (raw.displayTitle || cleanString(raw.title) || cleanString(raw.company) || 'Oportunidade sem título');
    const company = cleanString(raw.company) || 'Empregador não identificado';
    const category = cleanString(raw.category);
    const subtitle = verificationState === 'employer_lead'
      ? [category || 'Estabelecimento local', 'Prospecção'].join(' · ')
      : [company, category].filter(Boolean).join(' · ');

    const payText = raw.pay_display
      ? raw.pay_display
      : (typeof raw.pay_hourly_min === 'number' && Number.isFinite(raw.pay_hourly_min)
          ? `$${raw.pay_hourly_min}/h`
          : 'Não informado');
    const employmentTypeText = cleanString(raw.employment_type) || 'Não informado';
    const requirementsList = Array.isArray(raw.tickets_list) && raw.tickets_list.length > 0
      ? raw.tickets_list.slice()
      : null;
    const requirementsText = requirementsList ? requirementsList.join(', ') : 'Não informado';

    const commuteText = formatCommuteSummary(raw, context.commuteWindow);
    const commuteStale = isCommuteStale(raw, nowSec);
    const publishedText = formatDateDisplay(raw.published_at || raw.posted_date) || 'Não informado';
    const lastVerifiedText = formatDateDisplay(raw.last_verified_at) || 'Não verificado';

    const discoverySource = cleanString(raw.discovery_source) || cleanString(raw.source) || 'Desconhecida';
    const needsOsmAttribution =
      raw.discovery_source === 'OpenStreetMap discovery' || raw.source === 'OpenStreetMap discovery';

    const lifecycleState = LIFECYCLE_META[raw.lifecycle_state] ? raw.lifecycle_state : 'active';
    const lifecycleMeta = LIFECYCLE_META[lifecycleState];
    const sw = raw.specified_work_462 || {
      status: 'needs_review',
      rules_version: 'home-affairs-462-2026-11',
      reasons: [],
      missing_facts: ['Dados insuficientes para Subclass 462.'],
      disclaimer: 'Regras orientativas de trabalho especificado Subclass 462 — confirme sempre no Home Affairs.',
    };
    const specifiedWorkMeta = SPECIFIED_WORK_META[sw.status] || SPECIFIED_WORK_META.needs_review;

    const ctaUrl = cleanString(raw.source_url) || cleanString(raw.url) || cleanString(raw.employer_website);
    const locationText = cleanString(raw.suburb)
      ? `${raw.suburb}${raw.state ? ', ' + raw.state : ''}`
      : (cleanString(raw.location) || 'Localização não informada');

    return {
      id: raw.id,
      verification,
      title,
      company,
      subtitle,
      locationText,
      payText,
      employmentTypeText,
      requirementsList,
      requirementsText,
      commuteText,
      commuteStale,
      publishedText,
      lastVerifiedText,
      discoverySource,
      atsProvider: cleanString(raw.ats_provider),
      roleBucketLabel: raw.role_bucket && ROLE_BUCKET_LABELS[raw.role_bucket] ? ROLE_BUCKET_LABELS[raw.role_bucket] : cleanString(raw.role_bucket),
      needsOsmAttribution,
      ctaMode: verification.ctaMode,
      ctaLabel: verification.ctaLabel,
      ctaUrl,
      leadNotice:
        verificationState === 'employer_lead'
          ? 'Empresa identificada para prospecção local — sem vaga publicada confirmada.'
          : null,
      isFavorite: Boolean(userFavorites[raw.id]),
      status: userStatus[raw.id] || 'active',
      note: userNotes[raw.id] || '',
      match: raw.match || null,
      specifiedWork462: sw,
      specifiedWorkMeta,
      lifecycleState,
      lifecycleMeta,
      isStale: Boolean(raw.is_stale || lifecycleState === 'stale'),
      isExpired: lifecycleState === 'expired',
    };
  }

  return {
    VERIFICATION_META,
    SPECIFIED_WORK_META,
    LIFECYCLE_META,
    ROLE_BUCKET_LABELS,
    parseRequirementsList,
    normalizeOpportunity,
    filterOpportunities,
    computeSegmentCounts,
    sortOpportunities,
    buildJobDetailModel,
  };
});
