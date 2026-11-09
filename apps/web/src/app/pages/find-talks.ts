import { Component, computed, inject } from '@angular/core';

import { Icon } from '../icons';
import { SearchService } from '../search';
import { SessionCard } from '../session-card';

/** Searches worth trying, so an empty page shows what the Ask bar understands. */
const SUGGESTIONS = [
  'something about eBPF and security',
  'observability for beginners',
  'how do I stop an AI agent from breaking production',
  'rolling back a bad deploy',
];

/** Find talks: the results of the Ask bar's typed question, with honest empty, loading and error states. */
@Component({
  selector: 'app-find-talks',
  imports: [Icon, SessionCard],
  template: `
    <div class="page">
      <header class="page-header">
        <h1>Find talks</h1>
        <p class="lead">
          Search both events in your own words. Keywords and meaning side by side, over the official
          KubeCon schedule and the fictional CloudBridge archive, with every result labeled by its
          event.
        </p>
      </header>

      <p class="visually-hidden" role="status" aria-live="polite">{{ announcement() }}</p>

      @switch (search.status()) {
        @case ('idle') {
          <section class="card empty" aria-labelledby="try-heading">
            <h2 id="try-heading">Try asking</h2>
            <p class="muted">Type in the search field at the top, or start with one of these.</p>
            <ul class="suggestions">
              @for (suggestion of suggestions; track suggestion) {
                <li>
                  <button type="button" class="btn btn-outline" (click)="search.run(suggestion)">
                    <app-icon name="arrow-right" [size]="16" />
                    {{ suggestion }}
                  </button>
                </li>
              }
            </ul>
          </section>
        }
        @case ('searching') {
          <p class="muted">Searching for “{{ search.lastQuery() }}”…</p>
          <ol class="skeletons" aria-hidden="true">
            <li class="skeleton"></li>
            <li class="skeleton"></li>
            <li class="skeleton"></li>
          </ol>
        }
        @case ('error') {
          <div class="callout callout-danger" role="alert">
            <p><strong>Search isn't available right now.</strong> Please try again in a moment.</p>
            <button type="button" class="btn btn-outline" (click)="search.retry()">
              <app-icon name="refresh" [size]="18" />
              Try again
            </button>
          </div>
        }
        @case ('done') {
          @if (search.results().length === 0) {
            <section class="card empty" aria-labelledby="none-heading">
              <h2 id="none-heading">No talks match “{{ search.lastQuery() }}”</h2>
              <p class="muted">Try other words: a topic, a tool, or what you want to learn.</p>
            </section>
          } @else {
            <section aria-labelledby="results-heading" class="results-section">
              <div class="results-head">
                <h2 id="results-heading">Talks that match</h2>
                <p class="muted">{{ count() }} for “{{ search.lastQuery() }}”</p>
              </div>
              @if (search.retrievalMode() === 'lexical') {
                <p class="callout">
                  Matched by keywords only right now: the language model didn't answer in time.
                </p>
              }
              <ol class="results list">
                @for (result of search.results(); track result.id) {
                  <li><app-session-card [session]="result" /></li>
                }
              </ol>
            </section>
          }
        }
      }
    </div>
  `,
  styles: `
    .suggestions {
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      flex-wrap: wrap;
      gap: var(--space-2);
    }
    .suggestions .btn {
      font-weight: 500;
      text-align: left;
    }
    .results-section {
      display: grid;
      gap: var(--space-4);
    }
    .results-head {
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      justify-content: space-between;
      gap: var(--space-2);
    }
  `,
})
export class FindTalksPage {
  protected readonly search = inject(SearchService);
  protected readonly suggestions = SUGGESTIONS;

  protected readonly count = computed(() => {
    const n = this.search.results().length;
    return `${n} ${n === 1 ? 'talk' : 'talks'}`;
  });

  /** One short status line for screen readers, for the latest search; never announced per keystroke (A11Y-1). */
  protected readonly announcement = computed(() => {
    switch (this.search.status()) {
      case 'searching':
        return 'Searching…';
      case 'done': {
        const n = this.search.results().length;
        return n === 0
          ? 'No talks found. Try other words.'
          : `${n} ${n === 1 ? 'talk' : 'talks'} found.`;
      }
      default:
        return '';
    }
  });
}
