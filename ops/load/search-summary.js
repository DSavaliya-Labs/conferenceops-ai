// The search capacity test's per-request counting and its end-of-test summary, imported by search-peak.js. It holds
// no k6 imports, so ops/load/search-summary.test.js runs it in plain Node (node --test).

// The statuses that get their own counter; search-peak.js creates the counters from this list, and the summary reads
// them back, so the two can't drift apart.
export const TRACKED_STATUSES = [200, 400, 404, 422, 429, 500, 502, 503, 504];

export function createRecorder({
  statusCounters,
  otherStatus,
  noResponse,
  timedOut,
  capacityRejected,
  hybridMode,
  lexicalMode,
  unknownMode,
  capacityCode,
  timeoutErrorCode,
}) {
  return function record(res) {
    if (res.status === 0) {
      noResponse.add(1);
      if (res.error_code === timeoutErrorCode) timedOut.add(1);
      return;
    }
    const counter = statusCounters[res.status];
    if (counter) counter.add(1);
    else otherStatus.add(1);

    if (res.status === 503 && typeof res.body === 'string' && res.body.indexOf(capacityCode) !== -1) {
      capacityRejected.add(1);
    }
    if (res.status === 200) {
      const mode = retrievalMode(res);
      if (mode === 'hybrid') hybridMode.add(1);
      else if (mode === 'lexical') lexicalMode.add(1);
      else unknownMode.add(1);
    }
  };
}

export function createSummaryHandler(settings) {
  return function handleSummary(data) {
    const report = buildReport(data, settings);
    return { stdout: `${renderText(report, settings.capacityCode)}\n${JSON.stringify(report)}\n` };
  };
}

function retrievalMode(res) {
  try {
    const body = res.json();
    return body && Array.isArray(body.results) ? body.retrievalMode : undefined;
  } catch (e) {
    return undefined;
  }
}

function buildReport(data, settings) {
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
    target: `POST ${settings.searchUrl}`,
    startedAt: utc(endedMs - data.state.testRunDurationMs),
    endedAt: utc(endedMs),
    offered: {
      ratePerSecond: settings.rate,
      duration: settings.duration,
      queries: settings.queryCount,
      newConnectionPerRequest: true,
      retries: 0,
      timeoutSeconds: settings.timeoutSeconds,
    },
    vus: { preAllocated: settings.preVus, max: settings.maxVus, peak: stat(m, 'vus_max', 'max') },
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

function renderText(r, capacityCode) {
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
      `with ${capacityCode}: ${r.rejected503.capacityExhausted}`,
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
