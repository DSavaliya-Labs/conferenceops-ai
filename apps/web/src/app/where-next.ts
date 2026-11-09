import { HttpClient } from '@angular/common/http';
import { Service, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { DEMO_PROFILE_HEADER } from './personas';

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

@Service()
export class WhereNextService {
  private readonly http = inject(HttpClient);

  /** The persona travels in a header and the server looks her up (AUTHZ-1); nothing personal goes in the URL
   *  (PRIV-1). */
  next(profileId: string, window: WhereNextWindow, limit = 3): Observable<WhereNextResponse> {
    return this.http.post<WhereNextResponse>(
      '/api/v1/recommendations/next',
      { window, limit },
      { headers: { [DEMO_PROFILE_HEADER]: profileId } },
    );
  }
}
