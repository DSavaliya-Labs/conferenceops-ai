// Search capacity test (PLAN.md §15.2): a fixed offered load at our estimated peak against discovery-api's
// POST /api/v1/search. Run it as a Kubernetes Job against the discovery ClusterIP Service (scripts/load-run.sh), never
// through a port-forward. ops/load/README.md explains the rate and how to calibrate. The per-request counting and the
// end-of-test summary live in search-summary.js.
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
import { TRACKED_STATUSES, createRecorder, createSummaryHandler } from './search-summary.js';

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

const record = createRecorder({
  statusCounters,
  otherStatus,
  noResponse,
  timedOut,
  capacityRejected,
  hybridMode,
  lexicalMode,
  unknownMode,
  capacityCode: CAPACITY_CODE,
  timeoutErrorCode: TIMEOUT_ERROR_CODE,
});

function positiveInt(name, fallback) {
  const raw = __ENV[name];
  if (raw === undefined || raw === '') return fallback;
  if (!/^[1-9][0-9]*$/.test(raw)) throw new Error(`${name} must be a positive integer, got "${raw}"`);
  return Number(raw);
}

// ---- End-of-test summary: readable text, then the same numbers as one JSON line. -------------------------------

export const handleSummary = createSummaryHandler({
  searchUrl: SEARCH_URL,
  rate: RATE,
  duration: DURATION,
  queryCount: queries.length,
  timeoutSeconds: TIMEOUT_SECONDS,
  preVus: PRE_VUS,
  maxVus: MAX_VUS,
  capacityCode: CAPACITY_CODE,
});
