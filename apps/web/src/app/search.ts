import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

/** One search result as the discovery API returns it. The search PR replaces this with the generated client
 *  (API-1). */
export interface SearchResult {
  id: string;
  title: string;
  event: string;
  startsAt: string;
  room?: string;
  level?: string;
}

export interface SearchResponse {
  results: SearchResult[];
  retrievalMode: string;
}

@Injectable({ providedIn: 'root' })
export class SearchService {
  private readonly http = inject(HttpClient);

  /** The query travels in the request body, never in the URL, so it can't reach logs or telemetry (PRIV-1). */
  search(query: string): Observable<SearchResponse> {
    return this.http.post<SearchResponse>('/api/v1/search', { query });
  }
}
