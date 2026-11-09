import { Component, computed, effect, inject, input, signal } from '@angular/core';

import { ReadAloudButton } from './read-aloud';
import { RouterLink } from '@angular/router';
import { Subscription } from 'rxjs';

import { AskCitation, AskResponse, AskService } from './ask';
import { clock } from './replay';

type Status = 'idle' | 'loading' | 'done' | 'error';

/** The cited answer under the Ask bar: what published CloudBridge transcripts say about the question, with each
 *  citation linking to the replay at that moment. The answer is labeled by who wrote it: the model, from the cited
 *  passages only, or nobody, when the best-matching passage is quoted instead (AI-1). */
@Component({
  selector: 'app-ask-answer',
  imports: [RouterLink, ReadAloudButton],
  templateUrl: './ask-answer.html',
  styleUrl: './ask-answer.css',
})
export class AskAnswer {
  private readonly service = inject(AskService);
  private request?: Subscription;

  /** The question just asked; empty means nothing to show. */
  readonly question = input('');

  protected readonly status = signal<Status>('idle');
  protected readonly response = signal<AskResponse | null>(null);
  protected readonly clock = clock;

  protected readonly citations = computed<AskCitation[]>(() => this.response()?.citations ?? []);

  /** What "Read aloud" says: the answer, then each cited moment (talk, speaker, where in the replay). */
  protected readonly speech = computed(() => {
    const r = this.response();
    if (!r) {
      return '';
    }
    const cites = this.citations().map(
      (c) => `${c.sessionTitle}, ${c.speaker}, ${this.range(c)}: ${c.excerpt}`,
    );
    return [r.answer, cites.length ? `Cited moments: ${cites.join('. ')}` : '']
      .filter(Boolean)
      .join('. ');
  });

  protected readonly sourceLabel = computed(() => {
    const response = this.response();
    if (!response) {
      return '';
    }
    if (response.answerSource === 'model') {
      return 'Written by the model from the cited passages only; every citation was checked against the published transcript.';
    }
    return response.citations.length > 0
      ? 'Not model-written: the best-matching published passage, quoted. The model was slow, unavailable or cited nothing valid.'
      : 'Not model-written.';
  });

  /** One short line for screen readers once the answer is in; nothing while loading (A11Y-1). */
  protected readonly announcement = computed(() => {
    if (this.status() !== 'done') {
      return '';
    }
    const n = this.citations().length;
    return n === 0
      ? 'No answer from the archive.'
      : `Answer ready, ${n} cited ${n === 1 ? 'moment' : 'moments'}.`;
  });

  constructor() {
    effect(() => {
      const question = this.question().trim();
      this.request?.unsubscribe();
      if (!question) {
        this.status.set('idle');
        this.response.set(null);
        return;
      }
      this.status.set('loading');
      this.request = this.service.ask(question).subscribe({
        next: (response) => {
          this.response.set(response);
          this.status.set('done');
        },
        error: () => {
          this.response.set(null);
          this.status.set('error');
        },
      });
    });
  }

  protected range(citation: AskCitation): string {
    return `${clock(citation.startSeconds)}–${clock(citation.endSeconds)}`;
  }
}
