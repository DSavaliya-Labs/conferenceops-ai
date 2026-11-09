import { Component, computed, input } from '@angular/core';

import { Icon } from './icons';
import { EventBadge } from './session-card';
import { WhereNextResponse, WhereNextWindow } from './where-next';

const WINDOW_LABELS: Record<WhereNextWindow, string> = {
  today: 'for the rest of today',
  tomorrow_morning: 'for tomorrow morning',
  any: 'for the rest of the event',
};

/** Why talks were left out, in words. Counts only: never another attendee's data. */
const EXCLUSION_LABELS: Record<string, string> = {
  STARTED: 'had already started',
  OUTSIDE_WINDOW: 'were outside the time you chose',
  NOT_OPEN: "can't simply be walked into (off-site, sold out, or separate registration required)",
  ON_AGENDA: 'are already on your agenda',
  AGENDA_CONFLICT: 'overlap a talk on your agenda',
  LEVEL_EXCLUDED: 'are at a level you left out',
  CAPTIONS_NOT_PUBLISHED: "don't have published captioning, which you asked for",
  NO_MATCH: "don't match your interests or history",
};

/** The "where should I go next?" cards: title, venue time, room, why, and the access facts the event publishes,
 *  each with its source link (ACCESS-1). Nothing here ever says step-free: the server sends only "not published". */
@Component({
  selector: 'app-where-next-cards',
  imports: [EventBadge, Icon],
  template: `
    @if (response(); as r) {
      <section class="where-next-results" aria-labelledby="where-next-results-heading">
        <div class="results-head">
          <h2 id="where-next-results-heading">
            @if (r.status === 'matches') {
              Suggestions {{ windowLabel() }}
            } @else {
              No talk fits {{ windowLabel() }}
            }
          </h2>
          <p class="as-of muted">As of {{ r.conferenceTimeVenue }}, event time.</p>
        </div>

        @for (notice of r.warnings; track notice.code) {
          <p class="callout callout-warn notice">
            <span
              >{{ notice.text }}
              @if (notice.sourceUrl) {
                <a class="ext" [href]="notice.sourceUrl" target="_blank" rel="noopener noreferrer"
                  >Official source<app-icon name="external" [size]="14" /><span
                    class="visually-hidden"
                  >
                    (opens in a new tab)</span
                  ></a
                >
              }
            </span>
          </p>
        }
        @if (r.retrievalMode === 'lexical') {
          <p class="callout notice">
            Matched by keywords only right now: the language model didn't answer in time.
          </p>
        }

        @if (r.status === 'no_fit') {
          <div class="card empty">
            @if (exclusions().length > 0) {
              <p>Of the talks we looked at:</p>
              <ul class="exclusions">
                @for (item of exclusions(); track item.code) {
                  <li>
                    {{ item.count }} {{ item.count === 1 ? 'talk' : 'talks' }} {{ item.label }}
                  </li>
                }
              </ul>
            }
            <p>Try another time, or search for a topic.</p>
          </div>
        } @else {
          <ol class="cards">
            @for (candidate of r.candidates; track candidate.sessionId; let i = $index) {
              <li class="card" [attr.aria-labelledby]="'card-' + candidate.sessionId">
                <article>
                  <div class="head">
                    <span class="rank" aria-hidden="true">{{ i + 1 }}</span>
                    <h3 [id]="'card-' + candidate.sessionId">{{ candidate.title }}</h3>
                    <app-event-badge [fictional]="candidate.isFictional" />
                  </div>
                  <p class="event-label">{{ candidate.event }}</p>
                  <dl class="facts">
                    <div>
                      <dt><app-icon name="clock" [size]="16" /> When</dt>
                      <dd>
                        <time [attr.datetime]="candidate.startsAt">{{
                          candidate.startsAtVenue
                        }}</time>
                      </dd>
                    </div>
                    <div>
                      <dt><app-icon name="pin" [size]="16" /> Room</dt>
                      <dd>{{ candidate.room ?? 'Not listed in the schedule' }}</dd>
                    </div>
                    @if (candidate.level) {
                      <div>
                        <dt><app-icon name="spark" [size]="16" /> Level</dt>
                        <dd class="level">{{ candidate.level }}</dd>
                      </div>
                    }
                  </dl>
                  <h4>Why this talk</h4>
                  <ul class="reasons">
                    @for (reason of candidate.reasons; track $index) {
                      <li>{{ reason.text }}</li>
                    }
                  </ul>
                  <h4>Access</h4>
                  @if (candidate.eligibility === 'unconfirmed') {
                    <p class="unconfirmed">
                      Not every access need you set can be confirmed from published information.
                    </p>
                  }
                  <ul class="access">
                    @for (statement of candidate.access; track $index) {
                      <li [class.not-published]="statement.status === 'not_published'">
                        {{ statement.text }}
                        @if (statement.location) {
                          ({{ statement.location }})
                        }
                        @if (statement.sourceUrl) {
                          <a
                            class="ext"
                            [href]="statement.sourceUrl"
                            target="_blank"
                            rel="noopener noreferrer"
                            >Official source<app-icon name="external" [size]="14" /><span
                              class="visually-hidden"
                            >
                              (opens in a new tab)</span
                            ></a
                          >
                        }
                      </li>
                    }
                  </ul>
                </article>
              </li>
            }
          </ol>
        }
      </section>
    }
  `,
  styles: `
    .where-next-results {
      display: grid;
      gap: var(--space-4);
    }
    .results-head {
      display: grid;
      gap: var(--space-1);
    }
    .notice {
      margin: 0;
    }
    .cards {
      list-style: none;
      padding: 0;
      margin: 0;
      display: grid;
      gap: var(--space-4);
    }
    .cards .card {
      display: grid;
      gap: var(--space-2);
    }
    .head {
      display: flex;
      flex-wrap: wrap;
      align-items: flex-start;
      gap: var(--space-2) var(--space-3);
    }
    .rank {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex: none;
      width: 1.75rem;
      height: 1.75rem;
      border-radius: var(--radius-full);
      background: var(--accent-soft);
      color: var(--accent);
      font-weight: 700;
      font-size: var(--text-sm);
    }
    h3 {
      flex: 1 1 14rem;
      min-width: 0;
      overflow-wrap: anywhere;
    }
    h4 {
      margin-top: var(--space-2);
      font-size: var(--text-sm);
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--ink-3);
    }
    .event-label {
      color: var(--ink-3);
      font-size: var(--text-sm);
    }
    .facts {
      display: flex;
      flex-wrap: wrap;
      gap: var(--space-2) var(--space-5);
      margin: 0;
    }
    .facts div {
      display: grid;
      gap: 0.125rem;
    }
    dt {
      display: inline-flex;
      align-items: center;
      gap: var(--space-1);
      font-size: var(--text-xs);
      font-weight: 600;
      color: var(--ink-3);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    dd {
      margin: 0;
      font-weight: 500;
    }
    .level {
      text-transform: capitalize;
    }
    .reasons,
    .access,
    .exclusions {
      margin: 0;
      padding-left: 1.25rem;
      display: grid;
      gap: var(--space-1);
      list-style: disc;
    }
    .not-published {
      font-weight: 600;
    }
    .unconfirmed {
      color: var(--ink-2);
      font-size: var(--text-sm);
    }
  `,
})
export class WhereNextCards {
  readonly response = input<WhereNextResponse | null>(null);

  protected readonly windowLabel = computed(() => {
    const r = this.response();
    return r ? WINDOW_LABELS[r.window] : '';
  });

  protected readonly exclusions = computed(() =>
    Object.entries(this.response()?.excluded ?? {})
      .filter(([, count]) => count > 0)
      .map(([code, count]) => ({ code, count, label: EXCLUSION_LABELS[code] ?? 'were left out' })),
  );
}
