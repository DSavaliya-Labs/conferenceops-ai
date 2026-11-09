import { Component, computed, input } from '@angular/core';

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
  template: `
    @if (response(); as r) {
      <section class="where-next-results" aria-labelledby="where-next-results-heading">
        <h2 id="where-next-results-heading">
          @if (r.status === 'matches') {
            Suggestions {{ windowLabel() }}
          } @else {
            No talk fits {{ windowLabel() }}
          }
        </h2>
        <p class="as-of">As of {{ r.conferenceTimeVenue }}, event time.</p>

        @for (notice of r.warnings; track notice.code) {
          <p class="notice">
            {{ notice.text }}
            @if (notice.sourceUrl) {
              <a [href]="notice.sourceUrl" target="_blank" rel="noopener noreferrer"
                >Official source<span class="visually-hidden"> (opens in a new tab)</span></a
              >
            }
          </p>
        }
        @if (r.retrievalMode === 'lexical') {
          <p class="notice">
            Matched by keywords only right now: the language model didn't answer in time.
          </p>
        }

        @if (r.status === 'no_fit') {
          @if (exclusions().length > 0) {
            <p>Of the talks we looked at:</p>
            <ul class="exclusions">
              @for (item of exclusions(); track item.code) {
                <li>{{ item.count }} {{ item.count === 1 ? 'talk' : 'talks' }} {{ item.label }}</li>
              }
            </ul>
          }
          <p>Try another time, or search for a topic.</p>
        } @else {
          <ol class="cards">
            @for (candidate of r.candidates; track candidate.sessionId) {
              <li class="card">
                <article [attr.aria-labelledby]="'card-' + candidate.sessionId">
                  <h3 [id]="'card-' + candidate.sessionId">{{ candidate.title }}</h3>
                  <p class="event-label">
                    {{ candidate.event }} ·
                    <span class="badge">{{
                      candidate.isFictional
                        ? 'Fictional demo event'
                        : 'Real event, official schedule'
                    }}</span>
                  </p>
                  <dl class="facts">
                    <dt>When</dt>
                    <dd>
                      <time [attr.datetime]="candidate.startsAt">{{
                        candidate.startsAtVenue
                      }}</time>
                    </dd>
                    <dt>Room</dt>
                    <dd>{{ candidate.room ?? 'Not listed in the schedule' }}</dd>
                    @if (candidate.level) {
                      <dt>Level</dt>
                      <dd>{{ candidate.level }}</dd>
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
                          <a [href]="statement.sourceUrl" target="_blank" rel="noopener noreferrer"
                            >Official source<span class="visually-hidden">
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
    .as-of {
      color: #3f3f50;
      margin-top: 0;
    }
    .notice {
      border-left: 4px solid #1d4ed8;
      padding: 0.25rem 0.75rem;
      background: #eef2ff;
    }
    .cards {
      list-style: none;
      padding: 0;
    }
    .card {
      border: 1px solid #8a8a9e;
      border-radius: 0.5rem;
      padding: 0.75rem 1rem;
      margin-bottom: 1rem;
    }
    h3 {
      margin: 0 0 0.25rem;
      font-size: 1.15rem;
    }
    h4 {
      margin: 0.75rem 0 0.25rem;
      font-size: 1rem;
    }
    .event-label {
      margin: 0;
      color: #3f3f50;
    }
    .badge {
      font-weight: 600;
    }
    .facts {
      display: grid;
      grid-template-columns: max-content 1fr;
      gap: 0.15rem 0.75rem;
      margin: 0.5rem 0 0;
    }
    .facts dt {
      font-weight: 600;
    }
    .facts dd {
      margin: 0;
    }
    .reasons,
    .access,
    .exclusions {
      margin: 0;
      padding-left: 1.25rem;
    }
    .not-published {
      font-weight: 600;
    }
    .unconfirmed {
      margin: 0 0 0.25rem;
    }
    a:focus-visible {
      outline: 3px solid #1d4ed8;
      outline-offset: 2px;
    }
    .visually-hidden {
      position: absolute;
      width: 1px;
      height: 1px;
      overflow: hidden;
      clip-path: inset(50%);
      white-space: nowrap;
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
