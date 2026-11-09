import { Component, DestroyRef, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Subscription } from 'rxjs';

import { Icon } from '../icons';
import { EventCard, PortalService, formatDayRange } from '../portal';
import { EventBadge } from '../session-card';

type EventsStatus = 'loading' | 'done' | 'error';

/** Events: the two conferences on the platform, one real and one fictional, with what the platform holds for each. */
@Component({
  selector: 'app-events',
  imports: [EventBadge, Icon, RouterLink],
  template: `
    <div class="page">
      <header class="page-header">
        <h1>Events</h1>
        <p class="lead">
          Two events share this platform. KubeCon + CloudNativeCon North America 2026 is real,
          imported from its public schedule. CloudBridge Summit is fictional: the archive that gives
          the demo personas a history.
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
              <strong>The events aren't available right now.</strong> Please try again in a moment.
            </p>
            <button type="button" class="btn btn-outline" (click)="load()">
              <app-icon name="refresh" [size]="18" />
              Try again
            </button>
          </div>
        }
        @case ('done') {
          <ol class="list events">
            @for (event of events(); track event.slug) {
              <li>
                <article class="card event" [attr.aria-labelledby]="'event-' + event.slug">
                  <div class="head">
                    <h2 [id]="'event-' + event.slug">{{ event.name }}</h2>
                    <app-event-badge [fictional]="event.isFictional" />
                  </div>
                  <p class="dates">
                    <app-icon name="calendar" [size]="18" />
                    <span>{{ dates(event) }} · {{ timing(event) }} · {{ event.timezone }}</span>
                  </p>
                  <p class="about">
                    @if (event.isFictional) {
                      Authored on this platform for the demo. Every person, company and room in it
                      is invented, and it holds the personas' conference history.
                    } @else {
                      Imported from the event's public Sessionize feed, the one the official
                      schedule embeds. Real sessions, real speakers, real rooms; nothing is
                      generated for them.
                    }
                  </p>
                  <dl class="counts">
                    <div>
                      <dt>Talks</dt>
                      <dd>{{ event.counts.sessions }}</dd>
                    </div>
                    <div>
                      <dt>Speakers</dt>
                      <dd>{{ event.counts.speakers }}</dd>
                    </div>
                    <div>
                      <dt>Rooms</dt>
                      <dd>{{ event.counts.rooms }}</dd>
                    </div>
                    <div>
                      <dt>Published access facts</dt>
                      <dd>{{ event.counts.accessFacts }}</dd>
                    </div>
                  </dl>
                  <p class="links">
                    <a routerLink="/talks">Find talks</a>
                    <a routerLink="/accessibility">Accessibility at the venue</a>
                  </p>
                </article>
              </li>
            }
          </ol>
        }
      }
    </div>
  `,
  styles: `
    .event {
      display: grid;
      gap: var(--space-3);
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
    .dates {
      display: flex;
      align-items: center;
      gap: var(--space-2);
      color: var(--ink-2);
      font-weight: 500;
    }
    .about {
      color: var(--ink-2);
    }
    .counts {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(8rem, 1fr));
      gap: var(--space-3);
      margin: 0;
      padding: var(--space-3) 0 0;
      border-top: 1px solid var(--line);
    }
    .counts div {
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
    dd {
      margin: 0;
      font-size: var(--text-xl);
      font-weight: 700;
      font-variant-numeric: tabular-nums;
    }
    .links {
      display: flex;
      flex-wrap: wrap;
      gap: var(--space-4);
    }
  `,
})
export class EventsPage {
  private readonly portal = inject(PortalService);
  private request?: Subscription;

  protected readonly status = signal<EventsStatus>('loading');
  protected readonly events = signal<EventCard[]>([]);
  protected readonly announcement = signal('');

  constructor() {
    this.load();
    inject(DestroyRef).onDestroy(() => this.request?.unsubscribe());
  }

  protected load(): void {
    this.request?.unsubscribe();
    this.status.set('loading');
    this.announcement.set('');
    this.request = this.portal.events().subscribe({
      next: (list) => {
        this.events.set(list.events);
        this.status.set('done');
        this.announcement.set(`${list.events.length} events.`);
      },
      error: () => {
        this.events.set([]);
        this.status.set('error');
      },
    });
  }

  protected dates(event: EventCard): string {
    return formatDayRange(event.startsOn, event.endsOn);
  }

  /** Concluded, current or upcoming, by calendar day; the venue's clock decides nothing here. */
  protected timing(event: EventCard): string {
    const today = new Date().toISOString().slice(0, 10);
    if (event.endsOn < today) {
      return 'concluded';
    }
    return event.startsOn > today ? 'upcoming' : 'happening now';
  }
}
