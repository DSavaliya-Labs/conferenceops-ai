import assert from 'node:assert/strict';
import test from 'node:test';
import { createRecorder, createSummaryHandler } from './search-summary.js';

const counter = () => ({ count: 0, add(n) { this.count += n; } });

function summaryHandler() {
  return createSummaryHandler({
    searchUrl: 'http://conferenceops-discovery/api/v1/search',
    rate: 8,
    duration: '5m',
    queryCount: 12,
    timeoutSeconds: 10,
    preVus: 16,
    maxVus: 96,
    capacityCode: 'DISCOVERY_CAPACITY_EXHAUSTED',
  });
}

function readReport(result) {
  const line = result.stdout.trim().split('\n').at(-1);
  return JSON.parse(line);
}

test('records timeouts, response statuses, capacity rejections, and retrieval modes', () => {
  const statusCounters = { 200: counter(), 503: counter() };
  const metrics = {
    otherStatus: counter(),
    noResponse: counter(),
    timedOut: counter(),
    capacityRejected: counter(),
    hybridMode: counter(),
    lexicalMode: counter(),
    unknownMode: counter(),
  };
  const record = createRecorder({
    ...metrics,
    statusCounters,
    capacityCode: 'DISCOVERY_CAPACITY_EXHAUSTED',
    timeoutErrorCode: 1050,
  });

  record({ status: 0, error_code: 1050 });
  record({ status: 0, error_code: 0 });
  record({ status: 200, json: () => ({ results: [], retrievalMode: 'hybrid' }) });
  record({ status: 200, json: () => ({ results: [], retrievalMode: 'lexical' }) });
  record({ status: 200, json: () => { throw new Error('invalid response body'); } });
  record({ status: 503, body: 'DISCOVERY_CAPACITY_EXHAUSTED' });
  record({ status: 429 });

  assert.equal(metrics.noResponse.count, 2);
  assert.equal(metrics.timedOut.count, 1);
  assert.equal(statusCounters[200].count, 3);
  assert.equal(statusCounters[503].count, 1);
  assert.equal(metrics.capacityRejected.count, 1);
  assert.equal(metrics.otherStatus.count, 1);
  assert.equal(metrics.hybridMode.count, 1);
  assert.equal(metrics.lexicalMode.count, 1);
  assert.equal(metrics.unknownMode.count, 1);
});

test('summary reports request outcomes and threshold results', () => {
  const data = {
    state: { testRunDurationMs: 12500 },
    metrics: {
      http_reqs: { values: { count: 4 } },
      dropped_iterations: { values: { count: 1 } },
      http_req_failed: {
        values: { passes: 1, fails: 3 },
        thresholds: { 'rate<0.01': { ok: false } },
      },
      search_status_200: { values: { count: 3 } },
      search_status_503: { values: { count: 1 } },
      search_503_capacity_exhausted: { values: { count: 1 } },
      search_no_response: { values: { count: 1 } },
      search_timeout: { values: { count: 1 } },
      search_mode_hybrid: { values: { count: 2 } },
      search_mode_lexical: { values: { count: 1 } },
      search_mode_unknown: { values: { count: 0 } },
      vus_max: { values: { max: 16 } },
      http_req_duration: {
        values: { 'p(95)': 120.56 },
        thresholds: { 'p(95)<2000': { ok: true } },
      },
      'http_req_duration{expected_response:true}': {
        values: { 'p(95)': 115.44 },
        thresholds: { 'p(95)<1500': { ok: true } },
      },
    },
  };

  const result = summaryHandler()(data);
  const report = readReport(result);
  assert.equal(report.report, 'conferenceops-load-summary');
  assert.equal(report.target, 'POST http://conferenceops-discovery/api/v1/search');
  assert.equal(report.offered.ratePerSecond, 8);
  assert.equal(report.offered.queries, 12);
  assert.equal(report.runSeconds, 12.5);
  assert.equal(report.scheduled, 5);
  assert.equal(report.sent, 4);
  assert.equal(report.droppedIterations, 1);
  assert.equal(report.failed, 1);
  assert.equal(report.failedRate, 0.25);
  assert.deepEqual(report.status, { 200: 3, 503: 1 });
  assert.deepEqual(report.rejected503, { count: 1, rate: 0.25, capacityExhausted: 1 });
  assert.deepEqual(report.noResponse, { count: 1, timedOut: 1 });
  assert.deepEqual(report.retrievalMode, { hybrid: 2, lexical: 1, unknown: 0 });
  assert.deepEqual(report.p95Ms, { all: 120.6, successful: 115.4 });
  assert.deepEqual(
    Object.fromEntries(report.thresholds.map(({ metric, result: thresholdResult }) => [metric, thresholdResult])),
    {
      http_req_failed: 'fail',
      http_req_duration: 'pass',
      'http_req_duration{expected_response:true}': 'pass',
    },
  );
  assert.match(result.stdout, /with DISCOVERY_CAPACITY_EXHAUSTED: 1/);
  assert.match(result.stdout, /http_req_failed\s+25\.00%/);
});

test('empty runs keep undefined rates and latencies null and mark empty thresholds no data', () => {
  const data = {
    state: { testRunDurationMs: 1000 },
    metrics: {
      http_reqs: { values: { count: 0 } },
      dropped_iterations: { values: { count: 2 } },
      http_req_failed: {
        values: { passes: 0, fails: 0 },
        thresholds: { 'rate<0.01': { ok: true } },
      },
      http_req_duration: {
        values: {},
        thresholds: { 'p(95)<2000': { ok: true } },
      },
      'http_req_duration{expected_response:true}': {
        values: {},
        thresholds: { 'p(95)<1500': { ok: true } },
      },
    },
  };

  const result = summaryHandler()(data);
  const report = readReport(result);
  assert.equal(report.scheduled, 2);
  assert.equal(report.sent, 0);
  assert.equal(report.failedRate, null);
  assert.equal(report.rejected503.rate, null);
  assert.deepEqual(report.p95Ms, { all: null, successful: null });
  assert.deepEqual(report.thresholds.map((item) => item.result), ['no data', 'no data', 'no data']);
  assert.match(result.stdout, /successful n\/a/);
});
