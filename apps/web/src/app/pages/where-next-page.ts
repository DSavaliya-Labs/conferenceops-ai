import { Component, computed, inject } from '@angular/core';

import { Icon } from '../icons';
import { PersonaService } from '../personas';
import { WhereNextService, WhereNextWindow } from '../where-next';
import { WhereNextCards } from '../where-next-cards';

/** Where should I go next? The hero question: hard rules first, then ranking, with checkable reasons and only the
 *  access facts the event publishes (ACCESS-1). The answer comes from the service the app bar's button also uses. */
@Component({
  selector: 'app-where-next-page',
  imports: [Icon, WhereNextCards],
  template: `
    <div class="page">
      <header class="page-header">
        <h1>Where should I go next?</h1>
        <p class="lead">
          Talks that haven't started, picked from the persona's interests and CloudBridge history,
          with only the access facts the event publishes. Every suggestion says why.
        </p>
      </header>

      <form class="card where-next" (submit)="askWhereNext($event)" novalidate>
        <div class="where-next-row">
          <label for="where-next-window">When</label>
          <select
            id="where-next-window"
            name="window"
            class="control"
            (change)="onWindowChange($event)"
          >
            <option value="today" [selected]="whereNext.window() === 'today'">Rest of today</option>
            <option value="tomorrow_morning" [selected]="whereNext.window() === 'tomorrow_morning'">
              Tomorrow morning
            </option>
            <option value="any" [selected]="whereNext.window() === 'any'">Any time</option>
          </select>
          <button
            id="where-next-button"
            type="submit"
            class="btn btn-accent"
            [disabled]="whereNext.status() === 'loading'"
            aria-describedby="where-next-hint"
          >
            <app-icon name="compass" [size]="18" />
            Where should I go next?
          </button>
        </div>
        <p id="where-next-hint" class="hint">
          @if (personas.selected(); as persona) {
            For {{ persona.displayName }}. Hard rules first: not started on the venue's clock,
            inside the window, not on or clashing with the agenda, not at a level left out, and
            captions published when asked for.
          } @else {
            Choose a demo persona in the account menu at the top right: the suggestions are hers.
          }
        </p>
        @if (whereNext.validation()) {
          <p class="error" role="alert">{{ whereNext.validation() }}</p>
        }
      </form>

      <p class="visually-hidden" role="status" aria-live="polite">{{ announcement() }}</p>

      @switch (whereNext.status()) {
        @case ('loading') {
          <p class="muted">Finding talks for {{ whereNext.personaName() }}…</p>
          <ol class="skeletons" aria-hidden="true">
            <li class="skeleton"></li>
            <li class="skeleton"></li>
          </ol>
        }
        @case ('error') {
          <div class="callout callout-danger" role="alert">
            <p>
              <strong>Suggestions aren't available right now.</strong> Please try again in a moment.
            </p>
            <button
              type="button"
              class="btn btn-outline"
              (click)="whereNext.ask(personas.selected())"
            >
              <app-icon name="refresh" [size]="18" />
              Try again
            </button>
          </div>
        }
      }

      <app-where-next-cards [response]="whereNext.response()" />
    </div>
  `,
  styles: `
    .where-next {
      display: grid;
      gap: var(--space-3);
    }
    .where-next-row {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: var(--space-2) var(--space-3);
    }
    .control {
      flex: 1 1 10rem;
      max-width: 16rem;
    }
    .btn {
      flex: 1 1 auto;
    }
  `,
})
export class WhereNextPage {
  protected readonly whereNext = inject(WhereNextService);
  protected readonly personas = inject(PersonaService);

  protected readonly announcement = computed(() => {
    const name = this.whereNext.personaName();
    switch (this.whereNext.status()) {
      case 'loading':
        return `Finding talks for ${name}…`;
      case 'done': {
        const n = this.whereNext.response()?.candidates.length ?? 0;
        return n === 0
          ? 'No talk fits right now. The reasons are below.'
          : `${n} ${n === 1 ? 'suggestion' : 'suggestions'} for ${name}.`;
      }
      default:
        return '';
    }
  });

  protected onWindowChange(event: Event): void {
    this.whereNext.window.set((event.target as HTMLSelectElement).value as WhereNextWindow);
  }

  protected askWhereNext(event: Event): void {
    event.preventDefault();
    this.whereNext.ask(this.personas.selected());
  }
}
