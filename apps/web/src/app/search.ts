import { HttpClient } from '@angular/common/http';
import { Service, inject, signal } from '@angular/core';
import { Observable, Subscription } from 'rxjs';

/** One search result as the discovery API returns it. Mirrors `SearchHit` in contracts/openapi.json (API-1); the
 *  web image builds from apps/web alone, so the type is restated here. */
export interface SearchResult {
  id: string;
  title: string;
  event: string;
  conference: string;
  isFictional: boolean;
  startsAt: string;
  startsAtVenue: string;
  room?: string | null;
  level?: string | null;
  topics: string[];
}

export type RetrievalMode = 'hybrid' | 'lexical';

export interface SearchResponse {
  results: SearchResult[];
  retrievalMode: RetrievalMode;
}

export type SearchStatus = 'idle' | 'searching' | 'done' | 'error';

/** The Ask bar's typed half: the HTTP call, and the state the Find talks page shows. The state lives here, not in a
 *  page, so the search field in the app bar and the results page agree, and results survive moving between pages. */
@Service()
export class SearchService {
  private readonly http = inject(HttpClient);
  private request?: Subscription;

  /** What is in the search field right now. */
  readonly query = signal('');
  readonly status = signal<SearchStatus>('idle');
  readonly results = signal<SearchResult[]>([]);
  readonly retrievalMode = signal<RetrievalMode>('hybrid');
  /** The words the current results (or error) answer. */
  readonly lastQuery = signal('');

  /** The query travels in the request body, never in the URL, so it can't reach logs or telemetry (PRIV-1). */
  search(query: string): Observable<SearchResponse> {
    return this.http.post<SearchResponse>('/api/v1/search', { query });
  }

  /** Search for `query`, dropping any search still in flight. Blank input is ignored; the search bar explains. */
  run(query: string): void {
    const words = query.trim();
    if (!words) {
      return;
    }
    this.request?.unsubscribe();
    this.query.set(words);
    this.lastQuery.set(words);
    this.status.set('searching');
    this.request = this.search(words).subscribe({
      next: (response) => {
        this.results.set(response.results);
        this.retrievalMode.set(response.retrievalMode);
        this.status.set('done');
      },
      error: () => {
        this.results.set([]);
        this.status.set('error');
      },
    });
  }

  /** Run the last search again, after an error. */
  retry(): void {
    this.run(this.lastQuery());
  }
}
