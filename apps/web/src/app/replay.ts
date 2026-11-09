import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

/** A caption cue of the replay, in seconds. */
export interface ReplayCue {
  start: number;
  end: number;
  text: string;
}

/** `GET /api/v1/sessions/{id}/replay` (contracts/api.d.ts, `ReplayView`). A processing transcript has no cues and no
 *  text: the server never sends unpublished words (TRANSCRIPT-1). */
export interface ReplayView {
  session: {
    id: string;
    title: string;
    speakers: string[];
    event: string;
    conference: string;
    isFictional: boolean;
    startsAtVenue: string;
  };
  media: { url: string; captionsUrl: string; durationSeconds: number; label: string };
  transcript: {
    status: 'processing' | 'published';
    revision: number;
    label: string;
    cues: ReplayCue[];
    text: string | null;
  };
}

@Injectable({ providedIn: 'root' })
export class ReplayService {
  private readonly http = inject(HttpClient);

  replay(sessionId: string): Observable<ReplayView> {
    return this.http.get<ReplayView>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/replay`);
  }
}

/** Seconds as m:ss, for cue times and citations. */
export function clock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
}
