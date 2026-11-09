import { Component, DestroyRef, inject, signal } from '@angular/core';
import { Subscription, forkJoin, of, switchMap } from 'rxjs';

import { Icon } from '../icons';
import { AccessFactCard, AccessFactList, PortalService, formatDay } from '../portal';
import { EventBadge } from '../session-card';

type PageStatus = 'loading' | 'done' | 'error';

/** The published kinds, in the order they are shown, with their headings. */
const KIND_LABELS: Record<string, string> = {
  captions: 'Captioning',
  reserved_seating: 'Reserved seating',
  quiet_room: 'Quiet rooms',
  prayer_room: 'Prayer room',
  staff_help: 'Help from staff',
  mobility_rental: 'Wheelchair and scooter rental',
};

interface FactGroup {
  kind: string;
  label: string;
  facts: AccessFactCard[];
}

/** Accessibility at the venue: only the access facts each event publishes, each with its source link and the date
 *  it was read (ACCESS-1). Nothing is inferred, and nothing is ever labeled step-free. */
@Component({
  selector: 'app-accessibility',
  imports: [EventBadge, Icon],
  template: `
    <div class="page">
      <header class="page-header">
        <h1>Accessibility at the venue</h1>
        <p class="lead">
          Only what each event publishes, with the source it was read from. This platform infers
          nothing: an event that publishes no access facts shows none, and anything unpublished
          stays unknown.
        </p>
      </header>

      <p class="visually-hidden" role="status" aria-live="polite">{{ announcement() }}</p>

      @switch (status()) {
        @case ('loading') {
          <ol class="skeletons" aria-hidden="true">
            <li class="skeleton"></li>
            <li class="skeleton"></li>
          </ol>
        }
        @case ('error') {
          <div class="callout callout-danger" role="alert">
            <p>
              <strong>The access facts aren't available right now.</strong> Please try again in a
              moment.
            </p>
            <button type="button" class="btn btn-outline" (click)="load()">
              <app-icon name="refresh" [size]="18" />
              Try again
            </button>
          </div>
        }
        @case ('done') {
          @for (section of sections(); track section.conference) {
            <section class="card event" [attr.aria-labelledby]="'facts-' + section.conference">
              <div class="head">
                <h2 [id]="'facts-' + section.conference">{{ section.event }}</h2>
                <app-event-badge [fictional]="section.isFictional" />
              </div>
              @if (section.facts.length === 0) {
                <p class="muted">
                  This event publishes no access facts on this platform, so none are shown.
                </p>
              } @else {
                <p class="muted">
                  {{ section.facts.length }} published
                  {{ section.facts.length === 1 ? 'fact' : 'facts' }}, read on
                  {{ readOn(section) }}. Each links to where the event publishes it.
                </p>
                @for (group of groups(section); track group.kind) {
                  <section
                    class="group"
                    [attr.aria-labelledby]="'kind-' + section.conference + '-' + group.kind"
                  >
                    <h3 [id]="'kind-' + section.conference + '-' + group.kind">
                      {{ group.label }}
                    </h3>
                    <ul class="facts">
                      @for (fact of group.facts; track fact.id) {
                        <li class="fact">
                          <p class="text">{{ fact.text }}</p>
                          @if (fact.location) {
                            <p class="location">
                              <app-icon name="pin" [size]="16" /> {{ fact.location }}
                            </p>
                          }
                          <p class="source">
                            <a
                              class="ext"
                              [href]="fact.sourceUrl"
                              target="_blank"
                              rel="noopener noreferrer"
                              >Official source<app-icon name="external" [size]="14" /><span
                                class="visually-hidden"
                              >
                                (opens in a new tab)</span
                              ></a
                            >
                          </p>
                        </li>
                      }
                    </ul>
                  </section>
                }
                <div class="callout callout-warn">
                  <p>
                    <strong>Not published:</strong> step-free routes, walk times and room
                    capacities. This platform never claims them, so no talk or room is ever labeled
                    step-free.
                    @if (staffHelp(section); as help) {
                      {{ help.text }}
                    }
                  </p>
                </div>
              }
            </section>
          }
        }
      }
    </div>
  `,
  styles: `
    .event {
      display: grid;
      gap: var(--space-4);
    }
    .head {
      display: flex;
      flex-wrap: wrap;
      align-items: flex-start;
      justify-content: space-between;
      gap: var(--space-2) var(--space-3);
    }
    .head h2 {
      flex: 1 1 16rem;
      min-width: 0;
    }
    .group {
      display: grid;
      gap: var(--space-2);
      padding-top: var(--space-3);
      border-top: 1px solid var(--line);
    }
    .facts {
      list-style: none;
      margin: 0;
      padding: 0;
      display: grid;
      gap: var(--space-3);
    }
    .fact {
      display: grid;
      gap: var(--space-1);
    }
    .text {
      font-weight: 500;
    }
    .location {
      display: flex;
      align-items: center;
      gap: var(--space-1);
      color: var(--ink-2);
      font-size: var(--text-sm);
    }
    .source {
      font-size: var(--text-sm);
    }
  `,
})
export class AccessibilityPage {
  private readonly portal = inject(PortalService);
  private request?: Subscription;

  protected readonly status = signal<PageStatus>('loading');
  protected readonly sections = signal<AccessFactList[]>([]);
  protected readonly announcement = signal('');

  constructor() {
    this.load();
    inject(DestroyRef).onDestroy(() => this.request?.unsubscribe());
  }

  protected load(): void {
    this.request?.unsubscribe();
    this.status.set('loading');
    this.announcement.set('');
    this.request = this.portal
      .events()
      .pipe(
        switchMap((list) =>
          list.events.length === 0
            ? of([] as AccessFactList[])
            : forkJoin(list.events.map((event) => this.portal.accessFacts(event.slug))),
        ),
      )
      .subscribe({
        next: (sections) => {
          this.sections.set(sections);
          this.status.set('done');
          const facts = sections.reduce((n, s) => n + s.facts.length, 0);
          this.announcement.set(
            `${facts} published access ${facts === 1 ? 'fact' : 'facts'} loaded.`,
          );
        },
        error: () => {
          this.sections.set([]);
          this.status.set('error');
        },
      });
  }

  protected groups(section: AccessFactList): FactGroup[] {
    const order = Object.keys(KIND_LABELS);
    const byKind = new Map<string, AccessFactCard[]>();
    for (const fact of section.facts) {
      byKind.set(fact.kind, [...(byKind.get(fact.kind) ?? []), fact]);
    }
    return [...byKind.entries()]
      .sort(
        ([a], [b]) =>
          (order.indexOf(a) === -1 ? 99 : order.indexOf(a)) -
          (order.indexOf(b) === -1 ? 99 : order.indexOf(b)),
      )
      .map(([kind, facts]) => ({
        kind,
        label: KIND_LABELS[kind] ?? kind.replace(/_/g, ' '),
        facts,
      }));
  }

  protected staffHelp(section: AccessFactList): AccessFactCard | undefined {
    return section.facts.find((fact) => fact.kind === 'staff_help');
  }

  protected readOn(section: AccessFactList): string {
    const dates = [...new Set(section.facts.map((fact) => fact.readOn))].sort();
    return dates.map(formatDay).join(' and ');
  }
}
