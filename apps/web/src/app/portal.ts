import { HttpClient } from '@angular/common/http';
import { Service, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { DEMO_PROFILE_HEADER } from './personas';

/* The portal's read contract on core-api. These mirror the schemas in contracts/openapi.json (API-1); the web image
 * builds from apps/web alone, so the types are restated here. */

export interface EventCounts {
  sessions: number;
  speakers: number;
  rooms: number;
  accessFacts: number;
}

export interface EventCard {
  slug: string;
  name: string;
  startsOn: string;
  endsOn: string;
  timezone: string;
  isFictional: boolean;
  hasTranscripts: boolean;
  counts: EventCounts;
}

export interface EventList {
  events: EventCard[];
}

export interface AccessFactCard {
  id: string;
  kind: string;
  text: string;
  location?: string | null;
  sourceUrl: string;
  readOn: string;
}

export interface AccessFactList {
  conference: string;
  event: string;
  isFictional: boolean;
  facts: AccessFactCard[];
}

export interface AgendaEntry {
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
  topics: string[];
}

export interface AgendaResponse {
  profileId: string;
  displayName: string;
  bookmarked: AgendaEntry[];
  attended: AgendaEntry[];
}

/** "Nov 9, 2026" from an ISO date. The date is a calendar day, so it is read as one, never shifted by a zone. */
export function formatDay(iso: string): string {
  const [year, month, day] = iso.split('-').map(Number);
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(new Date(Date.UTC(year, month - 1, day)));
}

/** "Nov 9–12, 2026", or "Oct 30 – Nov 2, 2026" across months, from two ISO dates. */
export function formatDayRange(startIso: string, endIso: string): string {
  const [sy, sm, sd] = startIso.split('-').map(Number);
  const [ey, em, ed] = endIso.split('-').map(Number);
  const start = new Date(Date.UTC(sy, sm - 1, sd));
  const end = new Date(Date.UTC(ey, em - 1, ed));
  const monthDay = new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  });
  if (startIso === endIso) {
    return formatDay(startIso);
  }
  if (sy === ey && sm === em) {
    return `${monthDay.format(start)}–${ed}, ${ey}`;
  }
  return `${monthDay.format(start)} – ${monthDay.format(end)}, ${ey}`;
}

/** The portal's reads (events, published access facts, the persona's agenda) and the agenda's two writes. */
@Service()
export class PortalService {
  private readonly http = inject(HttpClient);

  events(): Observable<EventList> {
    return this.http.get<EventList>('/api/v1/conferences');
  }

  accessFacts(slug: string): Observable<AccessFactList> {
    return this.http.get<AccessFactList>(
      `/api/v1/conferences/${encodeURIComponent(slug)}/access-facts`,
    );
  }

  /** The persona travels in the header; the server resolves her (AUTHZ-1). Nothing personal goes in the URL. */
  agenda(profileId: string): Observable<AgendaResponse> {
    return this.http.get<AgendaResponse>('/api/v1/me/agenda', {
      headers: { [DEMO_PROFILE_HEADER]: profileId },
    });
  }

  /** Bookmark a talk for the persona: idempotent, 204 on success, 404 for an unknown persona or talk. */
  bookmark(profileId: string, sessionId: string): Observable<void> {
    return this.http.put<void>(`/api/v1/me/agenda/${encodeURIComponent(sessionId)}`, null, {
      headers: { [DEMO_PROFILE_HEADER]: profileId },
    });
  }

  /** Remove a bookmark: idempotent, 204 on success. */
  unbookmark(profileId: string, sessionId: string): Observable<void> {
    return this.http.delete<void>(`/api/v1/me/agenda/${encodeURIComponent(sessionId)}`, {
      headers: { [DEMO_PROFILE_HEADER]: profileId },
    });
  }
}
