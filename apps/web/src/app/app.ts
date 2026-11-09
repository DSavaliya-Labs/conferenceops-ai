import { Component, computed, inject, signal } from '@angular/core';
import { RouterOutlet } from '@angular/router';

import { AskAnswer } from './ask-answer';
import { SearchResult, SearchService } from './search';

type Status = 'idle' | 'searching' | 'done' | 'error';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, AskAnswer],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {
  private readonly searchService = inject(SearchService);

  protected readonly query = signal('');
  protected readonly status = signal<Status>('idle');
  protected readonly results = signal<SearchResult[]>([]);
  protected readonly validation = signal('');
  /** The question the archive answers with citations (app-ask-answer); set on each submit. */
  protected readonly asked = signal('');

  /** One short status line for screen readers; never announced per keystroke (A11Y-1). */
  protected readonly announcement = computed(() => {
    switch (this.status()) {
      case 'searching':
        return 'Searching…';
      case 'done': {
        const n = this.results().length;
        return n === 0 ? 'No talks found. Try other words.' : `${n} ${n === 1 ? 'talk' : 'talks'} found.`;
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
      this.validation.set('Type what you are looking for, for example “Kubernetes security for beginners”.');
      return;
    }
    this.status.set('searching');
    this.asked.set(query);
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
}
