import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

/** One cited moment of a published transcript, as `POST /api/v1/ask` returns it (contracts/api.d.ts, `Citation`). */
export interface AskCitation {
  chunkId: string;
  sessionId: string;
  sessionTitle: string;
  speaker: string;
  startSeconds: number;
  endSeconds: number;
  replayPath: string;
  excerpt: string;
  label: string;
}

export interface AskResponse {
  answer: string;
  /** model: written by the model from the cited passages; passages: not model-written, a passage quoted. */
  answerSource: 'model' | 'passages';
  citations: AskCitation[];
  retrievalMode: 'hybrid' | 'lexical';
  event?: string | null;
  isFictional?: boolean | null;
}

@Injectable({ providedIn: 'root' })
export class AskService {
  private readonly http = inject(HttpClient);

  /** The question travels in the request body, never in the URL (PRIV-1). */
  ask(query: string): Observable<AskResponse> {
    return this.http.post<AskResponse>('/api/v1/ask', { query });
  }
}
