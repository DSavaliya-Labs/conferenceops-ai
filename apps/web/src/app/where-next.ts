import { HttpClient } from '@angular/common/http';
import { Service, inject, signal } from '@angular/core';
import { Observable, Subscription } from 'rxjs';

import { DEMO_PROFILE_HEADER, Persona } from './personas';

/* The "where should I go next?" contract: these mirror the schemas in contracts/openapi.json (API-1). The web image
 * builds from apps/web alone, so the types are restated here rather than imported from contracts/api.d.ts. */

export type WhereNextWindow = 'today' | 'tomorrow_morning' | 'any';

export interface AccessStatement {
  kind: string;
  status: 'published' | 'not_published';
  text: string;
  location?: string | null;
  sourceUrl?: string | null;
}

export interface Reason {
  code: 'INTEREST_MATCH' | 'HISTORY_MATCH' | 'BOOKMARK_MATCH' | 'CAPTIONS_PUBLISHED';
  text: string;
  interests?: string[];
  relatedSessionId?: string | null;
  similarity?: number | null;
}

export interface Candidate {
  sessionId: string;
  title: string;
  event: string;
  conference: string;
  isFictional: boolean;
  startsAt: string;
  endsAt: string;
  startsAtVenue: string;
  room?: string | null;
  level?: string | null;
  eligibility: 'confirmed' | 'unconfirmed';
  score: number;
  sourceRevision: number;
  access: AccessStatement[];
  reasons: Reason[];
  sourceIds: string[];
}

export interface Notice {
  code: 'STEP_FREE_NOT_PUBLISHED' | 'NO_SIGNALS';
  text: string;
  sourceUrl?: string | null;
}

export interface WhereNextResponse {
  requestId: string;
  status: 'matches' | 'no_fit';
  retrievalMode: 'hybrid' | 'lexical';
  conferenceTime: string;
  conferenceTimeVenue: string;
  observedAt: string;
  window: WhereNextWindow;
  candidates: Candidate[];
  excluded: Record<string, number>;
  warnings: Notice[];
}

export type WhereNextStatus = 'idle' | 'loading' | 'done' | 'error';

export const CHOOSE_PERSONA_FIRST =
  'Choose a demo persona first, in the account menu at the top right of the page.';

/** The Ask bar's other question. The HTTP call and the state the Where next page shows, so the button in the app
 *  bar and the page share one answer. */
@Service()
export class WhereNextService {
  private readonly http = inject(HttpClient);
  private request?: Subscription;

  readonly window = signal<WhereNextWindow>('today');
  readonly status = signal<WhereNextStatus>('idle');
  readonly response = signal<WhereNextResponse | null>(null);
  readonly validation = signal('');
  /** Whose suggestions the answer holds, for the announcement. */
  readonly personaName = signal('');

  /** The persona travels in a header and the server looks her up (AUTHZ-1); nothing personal goes in the URL
   *  (PRIV-1). */
  next(profileId: string, window: WhereNextWindow, limit = 3): Observable<WhereNextResponse> {
    return this.http.post<WhereNextResponse>(
      '/api/v1/recommendations/next',
      { window, limit },
      { headers: { [DEMO_PROFILE_HEADER]: profileId } },
    );
  }

  /** "Where should I go next?" for the persona. The server applies her hard rules and history (AUTHZ-1). */
  ask(persona: Persona | null): void {
    if (!persona) {
      this.validation.set(CHOOSE_PERSONA_FIRST);
      return;
    }
    this.validation.set('');
    this.request?.unsubscribe();
    this.response.set(null);
    this.status.set('loading');
    this.personaName.set(persona.displayName);
    this.request = this.next(persona.id, this.window()).subscribe({
      next: (response) => {
        this.response.set(response);
        this.status.set('done');
      },
      error: () => {
        this.response.set(null);
        this.status.set('error');
      },
    });
  }

  /** PLAN §4.1: a persona switch cancels the request in flight and clears the last persona's suggestions, so they can
   *  never show up under the new one. */
  clear(): void {
    this.request?.unsubscribe();
    this.request = undefined;
    this.response.set(null);
    this.status.set('idle');
    this.validation.set('');
    this.personaName.set('');
  }
}
