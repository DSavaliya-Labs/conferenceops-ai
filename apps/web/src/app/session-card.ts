import { Component, input } from '@angular/core';

import { Icon } from './icons';

/** The event label every card carries: KubeCon is real and comes from the official schedule; CloudBridge Summit is
 *  the fictional demo event (PLAN §1.4). Text, not color alone. */
@Component({
  selector: 'app-event-badge',
  template: `
    <span class="badge" [class.badge-real]="!fictional()" [class.badge-fictional]="fictional()">
      {{ fictional() ? 'Fictional demo event' : 'Real event, official schedule' }}
    </span>
  `,
})
export class EventBadge {
  readonly fictional = input.required<boolean>();
}

/** What a session card shows: the fields search results and agenda entries share. */
export interface SessionSummary {
  id: string;
  title: string;
  event: string;
  isFictional: boolean;
  startsAt: string;
  startsAtVenue: string;
  room?: string | null;
  level?: string | null;
  topics?: string[];
}

/** A talk: title, its event badge, the venue time the API already formatted in the event's zone (TIME-1), room,
 *  level and topics. The room comes from the schedule or says it isn't listed; nothing is guessed. */
@Component({
  selector: 'app-session-card',
  imports: [EventBadge, Icon],
  template: `
    <article class="card session" [attr.aria-labelledby]="'session-' + session().id">
      <div class="head">
        <h3 class="title" [id]="'session-' + session().id">{{ session().title }}</h3>
        <app-event-badge [fictional]="session().isFictional" />
      </div>
      <p class="event">{{ session().event }}</p>
      <dl class="facts">
        <div>
          <dt><app-icon name="clock" [size]="16" /> When</dt>
          <dd>
            <time [attr.datetime]="session().startsAt">{{ session().startsAtVenue }}</time>
          </dd>
        </div>
        <div>
          <dt><app-icon name="pin" [size]="16" /> Room</dt>
          <dd>{{ session().room || 'Not listed in the schedule' }}</dd>
        </div>
        @if (session().level) {
          <div>
            <dt><app-icon name="spark" [size]="16" /> Level</dt>
            <dd class="level">{{ session().level }}</dd>
          </div>
        }
      </dl>
      @if (session().topics?.length) {
        <ul class="chips" aria-label="Topics">
          @for (topic of session().topics; track topic) {
            <li class="chip">{{ topic }}</li>
          }
        </ul>
      }
      <ng-content />
    </article>
  `,
  styles: `
    .session {
      display: grid;
      gap: var(--space-3);
      transition:
        box-shadow var(--dur-base) var(--ease),
        border-color var(--dur-base) var(--ease);
    }
    .session:hover {
      box-shadow: var(--shadow-2);
      border-color: var(--line-strong);
    }
    .head {
      display: flex;
      flex-wrap: wrap;
      align-items: flex-start;
      justify-content: space-between;
      gap: var(--space-2) var(--space-3);
    }
    .title {
      flex: 1 1 16rem;
      min-width: 0;
      overflow-wrap: anywhere;
    }
    .event {
      color: var(--ink-3);
      font-size: var(--text-sm);
      margin-top: calc(-1 * var(--space-2));
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
      min-width: 0;
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
      overflow-wrap: anywhere;
    }
    .level {
      text-transform: capitalize;
    }
  `,
})
export class SessionCard {
  readonly session = input.required<SessionSummary>();
}
