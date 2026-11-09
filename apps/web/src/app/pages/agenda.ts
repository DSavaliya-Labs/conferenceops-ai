import {
  Component,
  ElementRef,
  afterRenderEffect,
  computed,
  inject,
  viewChild,
} from '@angular/core';

import { AgendaService } from '../agenda-state';
import { Icon } from '../icons';
import { PersonaService } from '../personas';
import { AgendaEntry } from '../portal';
import { SessionCard, SessionSummary } from '../session-card';

/** My agenda: the persona's bookmarks and the talks she attended, from core-api behind X-Demo-Profile-ID
 *  (AUTHZ-1). Switching persona reloads; nothing from the last persona lingers. */
@Component({
  selector: 'app-agenda',
  imports: [Icon, SessionCard],
  template: `
    <div class="page">
      <header class="page-header">
        <h1>My agenda</h1>
        <p class="lead">
          Bookmarked talks and the ones attended at CloudBridge Summit, for the persona you are
          viewing as. A bookmark is a personal plan: it never reserves a seat.
        </p>
      </header>

      <p class="visually-hidden" role="status" aria-live="polite">{{ announcement() }}</p>

      @switch (status()) {
        @case ('none') {
          <section class="card empty" aria-labelledby="pick-heading">
            <h2 id="pick-heading">Choose a persona to see an agenda</h2>
            <p class="muted">
              Pick one of the eight fictional demo personas in the account menu at the top right of
              the page.
            </p>
          </section>
        }
        @case ('loading') {
          <p class="muted">Loading the agenda…</p>
          <ol class="skeletons" aria-hidden="true">
            <li class="skeleton"></li>
            <li class="skeleton"></li>
          </ol>
        }
        @case ('error') {
          <div class="callout callout-danger" role="alert">
            <p>
              <strong>The agenda isn't available right now.</strong> Please try again in a moment.
            </p>
            <button type="button" class="btn btn-outline" (click)="load()">
              <app-icon name="refresh" [size]="18" />
              Try again
            </button>
          </div>
        }
        @case ('missing') {
          <div class="callout callout-warn" role="alert">
            <p>
              This persona isn't on the server right now. Choose another one in the account menu.
            </p>
          </div>
        }
        @case ('done') {
          @if (agenda(); as a) {
            <section class="group" aria-labelledby="bookmarked-heading">
              <h2 id="bookmarked-heading" tabindex="-1" #bookmarkedHeading>
                Bookmarked <span class="count">{{ a.bookmarked.length }}</span>
              </h2>
              @if (a.bookmarked.length === 0) {
                <p class="muted">{{ a.displayName }} hasn't bookmarked a talk yet.</p>
              } @else {
                <ol class="list bookmarked">
                  @for (entry of a.bookmarked; track entry.sessionId) {
                    <li><app-session-card [session]="summary(entry)" /></li>
                  }
                </ol>
              }
            </section>
            <section class="group" aria-labelledby="attended-heading">
              <h2 id="attended-heading">
                Attended <span class="count">{{ a.attended.length }}</span>
              </h2>
              @if (a.attended.length === 0) {
                <p class="muted">No attended talks on record for {{ a.displayName }}.</p>
              } @else {
                <ol class="list attended">
                  @for (entry of a.attended; track entry.sessionId) {
                    <li><app-session-card [session]="summary(entry)" /></li>
                  }
                </ol>
              }
            </section>
          }
        }
      }
    </div>
  `,
  styles: `
    .group {
      display: grid;
      gap: var(--space-3);
    }
    .count {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 1.75rem;
      height: 1.75rem;
      padding: 0 0.5rem;
      margin-left: var(--space-2);
      border-radius: var(--radius-full);
      background: var(--surface-2);
      color: var(--ink-2);
      font-size: var(--text-sm);
      font-variant-numeric: tabular-nums;
      vertical-align: middle;
    }
  `,
})
export class AgendaPage {
  protected readonly personas = inject(PersonaService);
  protected readonly shared = inject(AgendaService);
  private readonly bookmarkedHeading = viewChild<ElementRef<HTMLElement>>('bookmarkedHeading');

  /** One load per persona, shared with the quick-add buttons on every card (AgendaService). */
  protected readonly status = this.shared.status;
  protected readonly agenda = this.shared.agenda;

  protected readonly announcement = computed(() => {
    const status = this.status();
    if (status === 'loading') {
      return 'Loading the agenda…';
    }
    const change = this.shared.announcement();
    if (change) {
      return change;
    }
    if (status === 'done') {
      const a = this.agenda();
      return a
        ? `Agenda for ${a.displayName}: ${a.bookmarked.length} bookmarked, ${a.attended.length} attended.`
        : '';
    }
    return '';
  });

  constructor() {
    // When a removal destroys the focused card, focus moves to the Bookmarked heading instead of falling to the
    // body, and the page-level status line above announces the change (A11Y-1).
    afterRenderEffect(() => {
      this.agenda();
      const heading = this.bookmarkedHeading()?.nativeElement;
      if (heading && typeof document !== 'undefined' && document.activeElement === document.body) {
        heading.focus();
      }
    });
  }

  protected load(): void {
    this.shared.refresh();
  }

  protected summary(entry: AgendaEntry): SessionSummary {
    return { ...entry, id: entry.sessionId };
  }
}
