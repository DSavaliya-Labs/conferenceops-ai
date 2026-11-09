import { init } from '@dash0/sdk-web';

/** Runtime settings served by the web container at /config.json (never baked into the build). */
export interface RuntimeConfig {
  dash0Endpoint?: string;
  dash0Token?: string; // the ingest-only browser token (SEC-1)
  dash0Dataset?: string;
  environment?: string;
  version?: string;
}

/** Drop query strings and fragments from every URL attribute, so typed text can never reach telemetry (PRIV-1). */
export function scrubUrlAttributes(attributes: Record<string, unknown>): Record<string, unknown> {
  const scrubbed: Record<string, unknown> = { ...attributes };
  for (const [key, value] of Object.entries(attributes)) {
    if (key.endsWith('url.query') || key.endsWith('url.fragment')) {
      scrubbed[key] = 'REDACTED';
    } else if (key.endsWith('url.full') && typeof value === 'string') {
      scrubbed[key] = value.split(/[?#]/)[0];
    }
  }
  return scrubbed;
}

/** Start Dash0 browser monitoring when a token is configured. Session replay stays off: it records the DOM, and
 *  with it what attendees type. */
export function startBrowserMonitoring(config: RuntimeConfig): boolean {
  if (!config.dash0Endpoint || !config.dash0Token) {
    return false;
  }
  init({
    serviceName: 'web',
    serviceNamespace: 'conferenceops',
    serviceVersion: config.version,
    environment: config.environment,
    endpoint: { url: config.dash0Endpoint, authToken: config.dash0Token, dataset: config.dash0Dataset },
    enabledInstrumentations: ['@dash0/navigation', '@dash0/web-vitals', '@dash0/error', '@dash0/fetch', '@dash0/xhr'],
    urlAttributeScrubber: scrubUrlAttributes as never,
  });
  return true;
}

export async function loadRuntimeConfig(fetchFn: typeof fetch = fetch): Promise<RuntimeConfig> {
  try {
    const response = await fetchFn('/config.json', { cache: 'no-store' });
    return response.ok ? ((await response.json()) as RuntimeConfig) : {};
  } catch {
    return {}; // monitoring is optional; the app works without it
  }
}
