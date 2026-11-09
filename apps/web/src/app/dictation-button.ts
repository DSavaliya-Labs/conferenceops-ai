import { Component, ElementRef, OnDestroy, inject, input, output, signal, viewChild } from '@angular/core';

import { DictationService } from './dictation';

export type DictationState = 'idle' | 'recording' | 'transcribing' | 'done' | 'failed';

/** Containers in the order we prefer them: what the transcription model lists (webm, mp4), then Firefox's ogg
 *  fallback. The browser records the first one it supports, or its own default when it supports none. */
const PREFERRED_TYPES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus', 'audio/ogg'];

/** The app's own upload bound (PLAN §9.2); core-api enforces the same number. */
const MAX_BYTES = 10 * 1024 * 1024;

const TYPE_IT = 'You can still type your question.';

function describeMicrophoneError(error: unknown): string {
  switch ((error as { name?: string } | null)?.name) {
    case 'NotAllowedError':
    case 'PermissionDeniedError':
    case 'SecurityError':
      return `Microphone access was denied. ${TYPE_IT}`;
    case 'NotFoundError':
    case 'DevicesNotFoundError':
    case 'OverconstrainedError':
      return `No microphone was found. ${TYPE_IT}`;
    case 'NotReadableError':
    case 'AbortError':
      return `The microphone couldn't be started; another app may be using it. ${TYPE_IT}`;
    default:
      return `The microphone couldn't be started. ${TYPE_IT}`;
  }
}

/** The microphone next to the Ask bar (PLAN §9.2): record, stop, transcribe, then hand the text to the Ask bar for
 *  review. It never starts recording on its own and never submits anything. One button toggles between "Dictate" and
 *  "Stop recording", so keyboard focus stays put through the whole flow (A11Y-1); a polite live region says what is
 *  happening, once per state change (aria-live rather than role=status: the page's one status role stays the Ask bar's
 *  own announcement). */
@Component({
  selector: 'app-dictation-button',
  template: `
    <button
      #micButton
      type="button"
      class="mic"
      [class.recording]="state() === 'recording'"
      [attr.aria-disabled]="state() === 'transcribing' ? 'true' : null"
      aria-describedby="dictation-status"
      (click)="toggle()"
    >
      @if (state() === 'recording') {
        <span class="dot" aria-hidden="true"></span>
        Stop recording
      } @else if (state() === 'transcribing') {
        Transcribing…
      } @else {
        <svg class="icon" aria-hidden="true" viewBox="0 0 24 24" width="18" height="18">
          <path
            fill="currentColor"
            d="M12 15a4 4 0 0 0 4-4V6a4 4 0 0 0-8 0v5a4 4 0 0 0 4 4Zm6-4a6 6 0 0 1-12 0H4a8 8 0 0 0 7 7.93V22h2v-3.07A8 8 0 0 0 20 11h-2Z"
          />
        </svg>
        Dictate
      }
    </button>
    @if (state() === 'recording') {
      <button type="button" class="cancel" (click)="cancel()">Cancel</button>
    }
    <span
      id="dictation-status"
      class="status"
      aria-live="polite"
      aria-atomic="true"
      [class.error]="state() === 'failed'"
      >{{ message() }}</span
    >
  `,
  styles: `
    :host {
      display: contents;
    }
    button {
      font-size: 1rem;
      padding: 0.5rem 0.9rem;
      border-radius: 0.25rem;
      cursor: pointer;
      background: #ffffff;
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
    }
    .mic {
      color: #1d4ed8;
      border: 2px solid #1d4ed8;
    }
    .mic.recording {
      color: #a4161a;
      border-color: #a4161a;
      background: #fff1f1;
    }
    .mic[aria-disabled='true'] {
      color: #3f3f50;
      border-color: #5b5b6e;
      cursor: progress;
    }
    .cancel {
      color: #1a1a2e;
      border: 2px solid #3f3f50;
    }
    .dot {
      width: 0.6rem;
      height: 0.6rem;
      border-radius: 50%;
      background: #a4161a;
      animation: pulse 1.2s ease-in-out infinite;
    }
    @keyframes pulse {
      50% {
        opacity: 0.25;
      }
    }
    .status {
      flex-basis: 100%;
      color: #3f3f50;
    }
    .status.error {
      color: #a4161a;
      font-weight: 600;
    }
    :focus-visible {
      outline: 3px solid #1d4ed8;
      outline-offset: 2px;
    }
    @media (prefers-reduced-motion: reduce) {
      .dot {
        animation: none;
      }
    }
  `,
})
export class DictationButton implements OnDestroy {
  private readonly service = inject(DictationService);
  private readonly micButton = viewChild.required<ElementRef<HTMLButtonElement>>('micButton');

  /** The app's recording limit (PLAN §9.2): the recorder stops itself there and transcribes what it has. */
  readonly maxSeconds = input(60);
  /** The recognized text. The parent puts it in the Ask bar; the attendee reviews it and decides whether to search. */
  readonly transcribed = output<string>();

  protected readonly state = signal<DictationState>('idle');
  protected readonly message = signal('');

  private stream: MediaStream | null = null;
  private recorder: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  private discard = false;
  private starting = false;
  private limitTimer: ReturnType<typeof setTimeout> | null = null;

  protected toggle(): void {
    switch (this.state()) {
      case 'recording':
        this.stop();
        break;
      case 'transcribing':
        break; // aria-disabled rather than disabled, so the button keeps focus while we wait
      default:
        void this.start();
    }
  }

  protected async start(): Promise<void> {
    if (this.starting || this.recorder) return;
    this.starting = true;
    this.discard = false;
    try {
      const mediaDevices = navigator.mediaDevices;
      if (!mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
        this.fail(
          window.isSecureContext === false
            ? `Dictation needs a secure (https) connection or localhost, and this page isn't one. ${TYPE_IT}`
            : `This browser can't record audio. ${TYPE_IT}`,
        );
        return;
      }
      let stream: MediaStream;
      try {
        stream = await mediaDevices.getUserMedia({ audio: true });
      } catch (error) {
        if (!this.discard) this.fail(describeMicrophoneError(error));
        return;
      }
      if (this.discard) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      const mimeType = PREFERRED_TYPES.find((type) => MediaRecorder.isTypeSupported?.(type));
      let recorder: MediaRecorder;
      try {
        recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      } catch {
        stream.getTracks().forEach((track) => track.stop());
        this.fail(`This browser can't record audio. ${TYPE_IT}`);
        return;
      }
      this.stream = stream;
      this.recorder = recorder;
      this.chunks = [];
      recorder.addEventListener('dataavailable', (event) => {
        if (event.data.size > 0) {
          this.chunks.push(event.data);
        }
      });
      recorder.addEventListener('stop', () => this.finish());
      recorder.addEventListener('error', () => {
        this.discard = true;
        this.release();
        this.fail(`Recording failed. ${TYPE_IT}`);
      });
      recorder.start();
      this.state.set('recording');
      this.message.set(`Recording, up to ${this.maxSeconds()} seconds. Press Stop recording when you're done.`);
      this.limitTimer = setTimeout(() => this.stop(), this.maxSeconds() * 1000);
    } finally {
      this.starting = false;
    }
  }

  /** Stop and transcribe. The recorder's stop event delivers the last chunk and then calls finish(). */
  protected stop(): void {
    this.clearLimit();
    if (this.recorder?.state === 'recording') {
      this.recorder.stop();
    }
  }

  /** Throw the recording away: nothing is sent anywhere. Focus returns to the Dictate button (A11Y-1). */
  protected cancel(): void {
    this.discard = true;
    this.clearLimit();
    const recorder = this.recorder;
    this.release();
    if (recorder?.state === 'recording') {
      recorder.stop();
    }
    this.chunks = [];
    this.state.set('idle');
    this.message.set('Cancelled. Nothing was sent.');
    this.micButton().nativeElement.focus();
  }

  private finish(): void {
    const recorder = this.recorder;
    this.release();
    if (this.discard || !recorder) {
      return;
    }
    const type = recorder.mimeType || this.chunks[0]?.type || 'audio/webm';
    const recording = new Blob(this.chunks, { type });
    this.chunks = [];
    if (recording.size === 0) {
      this.fail('Nothing was recorded. Try again, or type your question.');
      return;
    }
    if (recording.size > MAX_BYTES) {
      this.fail('The recording is too large to send. Try a shorter question, or type it.');
      return;
    }
    this.state.set('transcribing');
    this.message.set('Transcribing…');
    this.service.transcribe(recording).subscribe({
      next: ({ text }) => {
        const recognized = text.trim();
        if (!recognized) {
          this.fail('Nothing was recognized. Try again closer to the microphone, or type your question.');
          return;
        }
        this.state.set('done');
        this.message.set('Done. Review the text in the Ask box, then press Search.');
        this.transcribed.emit(recognized);
      },
      error: (error: { status?: number }) =>
        this.fail(
          error?.status === 413
            ? 'The recording is too long to send. Try a shorter question, or type it.'
            : `Transcription isn't available right now. ${TYPE_IT}`,
        ),
    });
  }

  private fail(message: string): void {
    this.state.set('failed');
    this.message.set(message);
  }

  /** Let go of the microphone: the browser's recording indicator goes off, and nothing lingers in memory. */
  private release(): void {
    this.clearLimit();
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    this.recorder = null;
  }

  private clearLimit(): void {
    if (this.limitTimer !== null) {
      clearTimeout(this.limitTimer);
      this.limitTimer = null;
    }
  }

  ngOnDestroy(): void {
    this.discard = true;
    const recorder = this.recorder;
    this.release();
    if (recorder?.state === 'recording') {
      recorder.stop();
    }
  }
}
