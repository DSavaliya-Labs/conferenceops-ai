import { Component, computed, inject, signal } from '@angular/core';
import { Subscription } from 'rxjs';

import { PersonaSwitcher } from './persona-switcher';
import { PersonaService } from './personas';
import { SearchResult, SearchService } from './search';
import { WhereNextResponse, WhereNextService, WhereNextWindow } from './where-next';
import { WhereNextCards } from './where-next-cards';

type Status = 'idle' | 'searching' | 'done' | 'error';
type WhereNextStatus = 'idle' | 'loading' | 'done' | 'error';

@Component({
  selector: 'app-root',
  imports: [PersonaSwitcher, WhereNextCards],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {
  private readonly searchService = inject(SearchService);
  private readonly whereNextService = inject(WhereNextService);
  protected readonly personas = inject(PersonaService);

  protected readonly query = signal('');
  protected readonly status = signal<Status>('idle');
  protected readonly results = signal<SearchResult[]>([]);
  protected readonly validation = signal('');

  protected readonly window = signal<WhereNextWindow>('today');
  protected readonly whereNextStatus = signal<WhereNextStatus>('idle');
  protected readonly whereNext = signal<WhereNextResponse | null>(null);
  protected readonly whereNextValidation = signal('');
  private readonly lastAction = signal<'search' | 'whereNext'>('search');
  private whereNextRequest?: Subscription;

  /** One short status line for screen readers, for the latest action; never announced per keystroke (A11Y-1). */
  protected readonly announcement = computed(() =>
    this.lastAction() === 'whereNext' ? this.whereNextAnnouncement() : this.searchAnnouncement(),
  );

  private readonly searchAnnouncement = computed(() => {
    switch (this.status()) {
      case 'searching':
        return 'Searching…';
      case 'done': {
        const n = this.results().length;
        return n === 0
          ? 'No talks found. Try other words.'
          : `${n} ${n === 1 ? 'talk' : 'talks'} found.`;
      }
      default:
        return '';
    }
  });

  private readonly whereNextAnnouncement = computed(() => {
    const name = this.personas.selected()?.displayName ?? '';
    switch (this.whereNextStatus()) {
      case 'loading':
        return `Finding talks for ${name}…`;
      case 'done': {
        const n = this.whereNext()?.candidates.length ?? 0;
        return n === 0
          ? 'No talk fits right now. The reasons are below.'
          : `${n} ${n === 1 ? 'suggestion' : 'suggestions'} for ${name}.`;
      }
      default:
        return '';
    }
  });

  protected onInput(event: Event): void {
    this.query.set((event.target as HTMLInputElement).value);
    this.validation.set('');
  }

  protected submit(event: Event): void {
    event.preventDefault();
    const query = this.query().trim();
    if (!query) {
      this.validation.set(
        'Type what you are looking for, for example “Kubernetes security for beginners”.',
      );
      return;
    }
    this.lastAction.set('search');
    this.status.set('searching');
    this.searchService.search(query).subscribe({
      next: (response) => {
        this.results.set(response.results);
        this.status.set('done');
      },
      error: () => {
        this.results.set([]);
        this.status.set('error');
      },
    });
  }

  protected onWindowChange(event: Event): void {
    this.window.set((event.target as HTMLSelectElement).value as WhereNextWindow);
  }

  /** "Where should I go next?" for the selected persona. The server applies her hard rules and history (AUTHZ-1). */
  protected askWhereNext(event: Event): void {
    event.preventDefault();
    const persona = this.personas.selected();
    if (!persona) {
      this.whereNextValidation.set(
        'Choose a demo persona first, in “Viewing as” at the top of the page.',
      );
      return;
    }
    this.whereNextValidation.set('');
    this.whereNextRequest?.unsubscribe();
    this.lastAction.set('whereNext');
    this.whereNext.set(null);
    this.whereNextStatus.set('loading');
    this.whereNextRequest = this.whereNextService.next(persona.id, this.window()).subscribe({
      next: (response) => {
        this.whereNext.set(response);
        this.whereNextStatus.set('done');
      },
      error: () => {
        this.whereNext.set(null);
        this.whereNextStatus.set('error');
      },
    });
  }

  /** PLAN §4.1: a persona switch cancels the request in flight and clears the last persona's suggestions, so they can
   *  never show up under the new one. Focus stays on the switch. */
  protected onPersonaChanged(): void {
    this.whereNextRequest?.unsubscribe();
    this.whereNextRequest = undefined;
    this.whereNext.set(null);
    this.whereNextStatus.set('idle');
    this.whereNextValidation.set('');
  }
}
