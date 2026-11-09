import { HttpClient } from '@angular/common/http';
import { Component, DestroyRef, inject, signal } from '@angular/core';

import { Icon } from '../icons';

const REPO = 'https://github.com/DSavaliya-Labs/conferenceops-ai';

/** What the browser may know about the running deployment: the environment and the version (the commit the images
 *  were built from). The runtime config also carries the ingest-only Dash0 token; it is never shown (SEC-1). */
interface DeploymentInfo {
  environment?: string;
  version?: string;
}

interface Step {
  title: string;
  text: string;
  evidence?: { label: string; href: string };
}

/** The operations loop, as it exists in this repository today. Each step names the thing judges can open. */
const STEPS: Step[] = [
  {
    title: 'A pull request opens',
    text:
      'Gitar reviews it against the rules in AGENTS.md, pushes fixes when it can, and approves clean reviews. CI runs ' +
      'the eight required checks: quality, domain-tests, data-integration, api-contract, ui-smoke-accessibility, ' +
      'helm-policy, incident-scope and build-images. Low- and medium-risk pull requests auto-merge once both agree.',
    evidence: { label: 'Pull requests, with Gitar’s reviews', href: `${REPO}/pulls?q=is%3Apr` },
  },
  {
    title: 'A merge to main deploys',
    text:
      'GitHub Actions builds the backend and web images, tagged with the commit, upgrades the Helm release on AKS, ' +
      'checks that the public URL serves the app, its config and a search, and marks the deploy in Dash0.',
    evidence: {
      label: 'The deploy workflow',
      href: `${REPO}/blob/main/.github/workflows/deploy.yml`,
    },
  },
  {
    title: 'Attendees use the app, Dash0 watches',
    text:
      'core-api, discovery-api and the worker send OpenTelemetry traces, metrics and logs through the collector to ' +
      'the Dash0 dataset conferenceops; this page reports browser performance through the Dash0 Web SDK with an ' +
      'ingest-only token. What you type never reaches telemetry or logs. The dashboard is “ConferenceOps: attendee ' +
      'impact”.',
    evidence: {
      label: 'Dash0 as code: dashboard, checks, automation',
      href: `${REPO}/blob/main/observability/dash0/README.md`,
    },
  },
  {
    title: 'A check fails',
    text:
      '“Discovery is rejecting searches” fires when more than 10% of discovery-api requests fail over a minute; ' +
      '“Search is slow” when the p95 of a search stays above 1.5 seconds for two minutes. Above its per-pod limit, ' +
      'discovery-api answers 503 DISCOVERY_CAPACITY_EXHAUSTED at once instead of queueing, and this app says search ' +
      'isn’t available and offers a retry.',
    evidence: { label: 'The check rules', href: `${REPO}/tree/main/observability/dash0/checks` },
  },
  {
    title: 'Agent0 investigates and opens a fix pull request',
    text:
      'The automation “ConferenceOps: fix discovery capacity” may change only the discovery workload’s replicas or ' +
      'resources, in one file, labeled incident. The incident-scope check and Gitar’s incident rule hold it to that, ' +
      'and it is armed only for the incident run.',
    evidence: {
      label: 'The automation and its prompt',
      href: `${REPO}/tree/main/observability/dash0/automations`,
    },
  },
  {
    title: 'Radar’s AI SRE investigates the cluster',
    text: 'Read-only. Its findings become pull requests through the same review and checks, and the loop starts again.',
    evidence: {
      label: 'The Build log: one entry per closed pull request (issue #1)',
      href: `${REPO}/issues/1`,
    },
  },
];

const RULES = [
  [
    'ACCESS-1',
    'Only published access facts, each with its source link. Nothing is ever labeled step-free.',
  ],
  ['SPEAKER-1', 'No generated transcripts, quotes or live text for real KubeCon speakers.'],
  [
    'AI-1',
    'The model picks only from what retrieval returned, every ID is checked, and it fails open.',
  ],
  ['PRIV-1', 'What you type or say, and accessibility preferences, never reach telemetry or logs.'],
  [
    'AUTHZ-1',
    'The server resolves the persona from X-Demo-Profile-ID; it trusts nothing else the browser says.',
  ],
  ['TIME-1', 'UTC instants, shown in the venue’s IANA time zone, never a fixed offset.'],
  [
    'A11Y-1',
    'Keyboard first: visible focus, labels, restrained live regions, reduced motion, contrast and reflow.',
  ],
];

/** How this platform runs: the loop for judges, with the links that back each step, and this deployment's
 *  version. No claims beyond what the repository and the running app show. */
@Component({
  selector: 'app-operations',
  imports: [Icon],
  template: `
    <div class="page">
      <header class="page-header">
        <h1>How this platform runs</h1>
        <p class="lead">
          A conference app with an AI operations team behind it. The loop below is what this
          repository does today, with a link to the evidence for each step.
        </p>
      </header>

      <section aria-labelledby="loop-heading" class="section">
        <h2 id="loop-heading">The loop</h2>
        <ol class="loop">
          @for (step of steps; track step.title; let i = $index) {
            <li class="step">
              <span class="marker" aria-hidden="true">{{ i + 1 }}</span>
              <div class="card body">
                <h3>{{ step.title }}</h3>
                <p>{{ step.text }}</p>
                @if (step.evidence; as evidence) {
                  <a class="ext" [href]="evidence.href" target="_blank" rel="noopener noreferrer"
                    >{{ evidence.label }}<app-icon name="external" [size]="14" /><span
                      class="visually-hidden"
                    >
                      (opens in a new tab)</span
                    ></a
                  >
                }
              </div>
            </li>
          }
        </ol>
      </section>

      <section aria-labelledby="deploy-heading" class="section">
        <h2 id="deploy-heading">This deployment</h2>
        <dl class="card deployment">
          <div>
            <dt>Public URL</dt>
            <dd>
              <a href="http://conferenceops-ai.westus3.cloudapp.azure.com"
                >conferenceops-ai.westus3.cloudapp.azure.com</a
              >
            </dd>
          </div>
          <div>
            <dt>Environment</dt>
            <dd>{{ deployment()?.environment || 'not reported' }}</dd>
          </div>
          <div>
            <dt>Version</dt>
            <dd>
              @if (deployment()?.version; as version) {
                <a
                  class="ext mono"
                  [href]="repo + '/commit/' + version"
                  target="_blank"
                  rel="noopener noreferrer"
                  >{{ version.slice(0, 12) }}<app-icon name="external" [size]="14" /><span
                    class="visually-hidden"
                  >
                    (commit on GitHub, opens in a new tab)</span
                  ></a
                >
              } @else {
                not reported
              }
            </dd>
          </div>
          <div>
            <dt>Runs on</dt>
            <dd>
              AKS, namespace conferenceops: nginx for this app, core-api, discovery-api, a worker,
              PostgreSQL 17, Elasticsearch 9 and the OpenTelemetry Collector
            </dd>
          </div>
        </dl>
      </section>

      <section aria-labelledby="rules-heading" class="section">
        <h2 id="rules-heading">Rules this app keeps</h2>
        <dl class="card rules">
          @for (rule of rules; track rule[0]) {
            <div>
              <dt>
                <code>{{ rule[0] }}</code>
              </dt>
              <dd>{{ rule[1] }}</dd>
            </div>
          }
        </dl>
        <p class="muted">
          The full list, with how changes land, is in
          <a
            class="ext"
            [href]="repo + '/blob/main/AGENTS.md'"
            target="_blank"
            rel="noopener noreferrer"
            >AGENTS.md<app-icon name="external" [size]="14" /><span class="visually-hidden">
              (opens in a new tab)</span
            ></a
          >. Source:
          <a class="ext" [href]="repo" target="_blank" rel="noopener noreferrer"
            >github.com/DSavaliya-Labs/conferenceops-ai<app-icon name="external" [size]="14" /><span
              class="visually-hidden"
            >
              (opens in a new tab)</span
            ></a
          >.
        </p>
      </section>
    </div>
  `,
  styles: `
    .section {
      display: grid;
      gap: var(--space-3);
    }
    .loop {
      list-style: none;
      margin: 0;
      padding: 0;
      display: grid;
      gap: var(--space-3);
      position: relative;
    }
    .loop::before {
      content: '';
      position: absolute;
      left: 1.125rem;
      top: 1rem;
      bottom: 1rem;
      width: 2px;
      background: var(--line);
    }
    .step {
      display: grid;
      grid-template-columns: 2.25rem minmax(0, 1fr);
      gap: var(--space-3);
      align-items: start;
    }
    .marker {
      position: relative;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 2.25rem;
      height: 2.25rem;
      border-radius: var(--radius-full);
      background: var(--brand);
      color: var(--on-brand);
      font-weight: 700;
      box-shadow: 0 0 0 4px var(--bg);
    }
    .body {
      display: grid;
      gap: var(--space-2);
    }
    .body p {
      color: var(--ink-2);
    }
    .deployment,
    .rules {
      display: grid;
      gap: var(--space-3);
      margin: 0;
    }
    .deployment div,
    .rules div {
      display: grid;
      gap: 0.125rem;
    }
    dt {
      font-size: var(--text-xs);
      font-weight: 600;
      color: var(--ink-3);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .rules dt {
      text-transform: none;
      letter-spacing: 0;
    }
    dd {
      margin: 0;
      overflow-wrap: anywhere;
    }
    code,
    .mono {
      font-family: var(--font-mono);
      font-size: 0.9em;
    }
    code {
      padding: 0.05rem 0.4rem;
      border-radius: var(--radius-sm);
      background: var(--brand-soft);
      color: var(--brand-strong);
      font-weight: 600;
    }
  `,
})
export class OperationsPage {
  private readonly http = inject(HttpClient);
  protected readonly steps = STEPS;
  protected readonly rules = RULES;
  protected readonly repo = REPO;
  protected readonly deployment = signal<DeploymentInfo | null>(null);

  constructor() {
    const request = this.http.get<DeploymentInfo>('/config.json').subscribe({
      next: (config) =>
        this.deployment.set({ environment: config.environment, version: config.version }),
      error: () => this.deployment.set(null),
    });
    inject(DestroyRef).onDestroy(() => request.unsubscribe());
  }
}
