// Search capacity test (PLAN.md §15.2): a fixed offered load at our estimated peak against discovery-api's
// POST /api/v1/search. Run it as a Kubernetes Job against the discovery ClusterIP Service (scripts/load-run.sh), never
// through a port-forward. ops/load/README.md explains the rate and how to calibrate.
//
// - Each iteration sends exactly one request, on a new connection, and never retries.
// - The arrival rate is constant: errors and slow answers never change the offered load.
// - Thresholds are the §15.4 acceptance targets. They are reported in the summary and never abort the run.
//
// Env:
//   BASE_URL  discovery base URL (default http://conferenceops-discovery)
//   RATE      requests per second (default 8)
//   DURATION  how long to offer the load, e.g. 90s, 5m, 1h (default 5m)
//   PRE_VUS   VUs allocated before the start (default 2 x RATE)
//   MAX_VUS   VU ceiling (default (timeout + 2 s) x RATE, so the rate holds even if every request times out)
//
// PRIV-1: the queries are synthetic phrases from queries.json; no query text reaches the summary or a metric tag.

import http from 'k6/http';
import exec from 'k6/execution';
import { SharedArray } from 'k6/data';
import { Counter } from 'k6/metrics';

const TIMEOUT_SECONDS = 10;
const CAPACITY_CODE = 'DISCOVERY_CAPACITY_EXHAUSTED';
const TIMEOUT_ERROR_CODE = 1050; // k6: "request timeout"

const BASE_URL = (__ENV.BASE_URL || 'http://conferenceops-discovery').replace(/\/+$/, '');
const SEARCH_URL = `${BASE_URL}/api/v1/search`;
const RATE = positiveInt('RATE', 8);
const DURATION = __ENV.DURATION || '5m';
const PRE_VUS = positiveInt('PRE_VUS', Math.max(2, RATE * 2));
const MAX_VUS = positiveInt('MAX_VUS', Math.max(PRE_VUS, RATE * (TIMEOUT_SECONDS + 2)));

const queries = new SharedArray('search queries', () => {
  const list = JSON.parse(open('./queries.json'));
  const valid = Array.isArray(list) && list.length > 0 &&
    list.every((q) => typeof q === 'string' && q.trim().length > 0 && q.length <= 300);
  if (!valid) throw new Error('queries.json must be a non-empty array of strings of 1 to 300 characters');
  return list;
});

// Status-code counts for the summary. The 503 counter is the one the incident is about; a 503 that carries the
// admission-control code is also counted on its own, so capacity rejections are never confused with outages.
const TRACKED_STATUSES = [200, 400, 404, 422, 429, 500, 502, 503, 504];
const statusCounters = {};
for (const code of TRACKED_STATUSES) statusCounters[code] = new Counter(`search_status_${code}`);
const otherStatus = new Counter('search_status_other');
const noResponse = new Counter('search_no_response'); // status 0: timeout, refused, reset or DNS failure
const timedOut = new Counter('search_timeout');
const capacityRejected = new Counter('search_503_capacity_exhausted');
const hybridMode = new Counter('search_mode_hybrid');
const lexicalMode = new Counter('search_mode_lexical');
const unknownMode = new Counter('search_mode_unknown');

export const options = {
  scenarios: {
    search_peak: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: PRE_VUS,
      maxVUs: MAX_VUS,
    },
  },
  // A new connection per iteration, so the ClusterIP Service spreads requests across every Ready pod.
  noVUConnectionReuse: true,
  // Label the synthetic traffic, so it can be told apart from real attendees in the service's telemetry.
  userAgent: 'conferenceops-load/search-peak (k6)',
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
  // PLAN.md §15.4 targets. No abortOnFail: a crossed threshold is reported, and the offered load stays fixed.
  thresholds: {
    http_req_failed: ['rate<0.01'],
    'http_req_duration{expected_response:true}': ['p(95)<1500'],
    dropped_iterations: ['count<1'],
  },
};

export default function () {
  // A fixed rotation through the corpus, so every run offers the same query mix.
  const query = queries[exec.scenario.iterationInTest % queries.length];
  const res = http.post(SEARCH_URL, JSON.stringify({ query }), {
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    timeout: `${TIMEOUT_SECONDS}s`,
    tags: { name: 'POST /api/v1/search' },
  });
  record(res);
}

function record(res) {
  if (res.status === 0) {
    noResponse.add(1);
    if (res.error_code === TIMEOUT_ERROR_CODE) timedOut.add(1);
    return;
  }
  const counter = statusCounters[res.status];
  if (counter) counter.add(1);
  else otherStatus.add(1);

  if (res.status === 503 && typeof res.body === 'string' && res.body.indexOf(CAPACITY_CODE) !== -1) {
    capacityRejected.add(1);
  }
  if (res.status === 200) {
    const mode = retrievalMode(res);
    if (mode === 'hybrid') hybridMode.add(1);
    else if (mode === 'lexical') lexicalMode.add(1);
    else unknownMode.add(1);
  }
}

function retrievalMode(res) {
  try {
    const body = res.json();
    return body && Array.isArray(body.results) ? body.retrievalMode : undefined;
  } catch (e) {
    return undefined;
  }
}

function positiveInt(name, fallback) {
  const raw = __ENV[name];
  if (raw === undefined || raw === '') return fallback;
  if (!/^[1-9][0-9]*$/.test(raw)) throw new Error(`${name} must be a positive integer, got "${raw}"`);
  return Number(raw);
}

// ---- End-of-test summary: readable text, then the same numbers as one JSON line. -------------------------------

export function handleSummary(data) {
  const report = buildReport(data);
  return { stdout: `${renderText(report)}\n${JSON.stringify(report)}\n` };
}

function buildReport(data) {
  const m = data.metrics;
  const sent = count(m, 'http_reqs');
  const dropped = count(m, 'dropped_iterations');
  // http_req_failed is a Rate: "passes" counts failed requests, "fails" counts successful ones.
  const failed = m.http_req_failed ? m.http_req_failed.values.passes : 0;
  const successful = m.http_req_failed ? m.http_req_failed.values.fails : 0;
  const status = {};
  for (const code of TRACKED_STATUSES) {
    const n = count(m, `search_status_${code}`);
    if (n > 0) status[String(code)] = n;
  }
  const other = count(m, 'search_status_other');
  if (other > 0) status.other = other;
  const rejected503 = count(m, 'search_status_503');
  // The run's UTC window, to line the summary up with the service's telemetry. The summary runs right after the end.
  const endedMs = Date.now();
  const utc = (ms) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z');

  return {
    report: 'conferenceops-load-summary',
    test: 'search-peak',
    target: `POST ${SEARCH_URL}`,
    startedAt: utc(endedMs - data.state.testRunDurationMs),
    endedAt: utc(endedMs),
    offered: {
      ratePerSecond: RATE,
      duration: DURATION,
      queries: queries.length,
      newConnectionPerRequest: true,
      retries: 0,
      timeoutSeconds: TIMEOUT_SECONDS,
    },
    vus: { preAllocated: PRE_VUS, max: MAX_VUS, peak: stat(m, 'vus_max', 'max') },
    runSeconds: round(data.state.testRunDurationMs / 1000, 1),
    scheduled: sent + dropped,
    sent,
    droppedIterations: dropped,
    failed,
    failedRate: sent > 0 ? round(failed / sent, 4) : null,
    status,
    rejected503: {
      count: rejected503,
      rate: sent > 0 ? round(rejected503 / sent, 4) : null,
      capacityExhausted: count(m, 'search_503_capacity_exhausted'),
    },
    noResponse: { count: count(m, 'search_no_response'), timedOut: count(m, 'search_timeout') },
    retrievalMode: {
      hybrid: count(m, 'search_mode_hybrid'),
      lexical: count(m, 'search_mode_lexical'),
      unknown: count(m, 'search_mode_unknown'),
    },
    p95Ms: {
      all: sent > 0 ? round(stat(m, 'http_req_duration', 'p(95)'), 1) : null,
      successful: successful > 0 ? round(stat(m, 'http_req_duration{expected_response:true}', 'p(95)'), 1) : null,
    },
    thresholds: thresholdResults(m, {
      http_req_failed: sent,
      http_req_duration: sent,
      'http_req_duration{expected_response:true}': successful,
    }),
  };
}

function renderText(r) {
  const pct = (x) => (x === null ? 'n/a' : `${(x * 100).toFixed(2)}%`);
  const statusText = Object.keys(r.status).map((k) => `${k}: ${r.status[k]}`).join('   ') || 'none';
  const lines = [
    '',
    '== ConferenceOps search capacity test (ops/load/search-peak.js) ==',
    `Target              ${r.target}`,
    `Offered load        ${r.offered.ratePerSecond} req/s for ${r.offered.duration}, constant arrival rate; ` +
      `${r.offered.queries} queries in a fixed rotation`,
    `Connections         a new connection per request; no retries; ${r.offered.timeoutSeconds} s timeout`,
    `VUs                 ${r.vus.preAllocated} pre-allocated, ${r.vus.max} max, ${fmt(r.vus.peak)} allocated at peak`,
    `Run time            ${r.runSeconds} s, ${r.startedAt} to ${r.endedAt}`,
    '',
    `Scheduled           ${r.scheduled}`,
    `Sent                ${r.sent}`,
    `Dropped iterations  ${r.droppedIterations}  (scheduled but never sent: no free VU)`,
    `http_req_failed     ${pct(r.failedRate)}  (${r.failed} of ${r.sent} sent)`,
    `Status codes        ${statusText}`,
    `503 responses       ${r.rejected503.count}  (${pct(r.rejected503.rate)} of sent); ` +
      `with ${CAPACITY_CODE}: ${r.rejected503.capacityExhausted}`,
    `No response         ${r.noResponse.count}  (timed out: ${r.noResponse.timedOut})`,
    `200 by mode         hybrid ${r.retrievalMode.hybrid}, lexical ${r.retrievalMode.lexical}, ` +
      `unrecognized ${r.retrievalMode.unknown}`,
    `p95 latency         all ${ms(r.p95Ms.all)}; successful ${ms(r.p95Ms.successful)}`,
    '',
    'Thresholds (PLAN.md §15.4; reported only, they never stop the run)',
  ];
  for (const t of r.thresholds) lines.push(`  ${t.result.toUpperCase().padEnd(7)}  ${t.metric}  ${t.expression}`);
  if (r.thresholds.length === 0) lines.push('  none evaluated');
  lines.push('');
  return lines.join('\n');
}

// k6 passes a threshold whose metric has no samples (no successful request means no "successful p95"). Report
// that case as "no data" instead of a pass. `samples` maps a metric to its sample count when it is known.
function thresholdResults(m, samples) {
  const results = [];
  for (const metric of Object.keys(m).sort()) {
    const thresholds = m[metric].thresholds || {};
    for (const expression of Object.keys(thresholds)) {
      let result = thresholds[expression].ok ? 'pass' : 'fail';
      if (samples[metric] === 0) result = 'no data';
      results.push({ metric, expression, result });
    }
  }
  return results;
}

function count(m, name) {
  return m[name] && m[name].values && typeof m[name].values.count === 'number' ? m[name].values.count : 0;
}

function stat(m, name, key) {
  return m[name] && m[name].values && typeof m[name].values[key] === 'number' ? m[name].values[key] : null;
}

function round(x, digits) {
  if (x === null || x === undefined || Number.isNaN(x)) return null;
  const f = Math.pow(10, digits);
  return Math.round(x * f) / f;
}

function fmt(x) {
  return x === null ? 'n/a' : String(x);
}

function ms(x) {
  if (x === null) return 'n/a';
  return x >= 1000 ? `${(x / 1000).toFixed(2)} s` : `${Math.round(x)} ms`;
}
