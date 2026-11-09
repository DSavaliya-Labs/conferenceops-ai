import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

/** POST /api/v1/dictation's answer. It mirrors DictationResponse in contracts/openapi.json (API-1); the web image
 *  builds from apps/web alone, so the type is restated here rather than imported from contracts/api.d.ts. */
export interface DictationResponse {
  /** The recognized text, for the attendee to review; empty when nothing was recognized. */
  text: string;
}

@Injectable({ providedIn: 'root' })
export class DictationService {
  private readonly http = inject(HttpClient);

  /** The recording travels as the request body to core-api, which holds the OpenAI key (SEC-1). The body is the only
   *  place the audio goes: never a URL, never storage (PRIV-1). */
  transcribe(recording: Blob): Observable<DictationResponse> {
    return this.http.post<DictationResponse>('/api/v1/dictation', recording, {
      headers: { 'Content-Type': recording.type || 'audio/webm' },
    });
  }
}
