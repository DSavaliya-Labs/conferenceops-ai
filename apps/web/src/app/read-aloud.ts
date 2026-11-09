import { Component, Service, computed, inject, input, signal } from '@angular/core';

import { Icon } from './icons';
import { SessionSummary } from './session-card';

/** Spoken text for a talk card: the fields in reading order, with the venue time the API already formatted. */
export function sessionSpeech(s: SessionSummary): string {
  const parts = [
    s.title,
    s.event,
    s.startsAtVenue,
    `Room: ${s.room || 'not listed in the schedule'}`,
    s.level ? `Level: ${s.level}` : '',
    s.topics?.length ? `Topics: ${s.topics.join(', ')}` : '',
  ];
  return parts.filter(Boolean).join('. ') + '.';
}

interface VoiceLike {
  lang: string;
  localService: boolean;
  name: string;
}

interface UtteranceLike {
  lang: string;
  voice: VoiceLike | null;
  onend: (() => void) | null;
  onerror: (() => void) | null;
}

type Synth = {
  speak(utterance: unknown): void;
  cancel(): void;
  getVoices(): VoiceLike[];
  addEventListener?(type: string, listener: () => void): void;
};

/** Reads a card aloud with an on-device voice of the browser's speech synthesis (Web Speech API), so the text never
 *  leaves the device (PRIV-1: a card can carry a persona's access statements). Browsers also offer cloud voices,
 *  whose synthesis runs on a vendor's servers; those are never used. Without an on-device voice the feature is
 *  unavailable and the button says so. One card speaks at a time. */
@Service()
export class ReadAloudService {
  private readonly synth: Synth | null =
    typeof window !== 'undefined' && 'speechSynthesis' in window
      ? (window as unknown as { speechSynthesis: Synth }).speechSynthesis
      : null;
  private readonly voices = signal<VoiceLike[]>(this.synth?.getVoices?.() ?? []);
  /** The on-device voice to use: English first, else any local one; null when the browser has none. */
  readonly localVoice = computed<VoiceLike | null>(() => {
    const local = this.voices().filter((v) => v.localService);
    return local.find((v) => v.lang.toLowerCase().startsWith('en')) ?? local[0] ?? null;
  });
  readonly supported = computed(() => this.synth !== null && this.localVoice() !== null);
  /** The card being read, or null. */
  readonly speaking = signal<string | null>(null);

  constructor() {
    // Chrome loads its voice list asynchronously and says so with this event.
    this.synth?.addEventListener?.('voiceschanged', () =>
      this.voices.set(this.synth?.getVoices() ?? []),
    );
  }

  toggle(id: string, text: string): void {
    const synth = this.synth;
    const voice = this.localVoice();
    if (!synth || !voice) {
      return;
    }
    if (this.speaking() === id) {
      this.stop();
      return;
    }
    synth.cancel();
    const Utterance = (
      window as unknown as { SpeechSynthesisUtterance: new (t: string) => UtteranceLike }
    ).SpeechSynthesisUtterance;
    const utterance = new Utterance(text);
    utterance.voice = voice;
    utterance.lang = voice.lang;
    utterance.onend = () => this.speaking.update((current) => (current === id ? null : current));
    utterance.onerror = utterance.onend;
    this.speaking.set(id);
    synth.speak(utterance);
  }

  stop(): void {
    this.synth?.cancel();
    this.speaking.set(null);
  }
}

/** "Read aloud" / "Stop reading" for one card. `aria-pressed` carries the state; the label names what is read. */
@Component({
  selector: 'app-read-aloud-button',
  imports: [Icon],
  template: `
    <button
      type="button"
      class="btn btn-outline read-aloud"
      [attr.aria-pressed]="on()"
      [attr.aria-describedby]="reader.supported() ? null : hintId()"
      [disabled]="!reader.supported()"
      (click)="reader.toggle(id(), text())"
    >
      <app-icon [name]="on() ? 'close' : 'activity'" [size]="16" />
      {{ on() ? 'Stop reading' : 'Read aloud' }}
      <span class="visually-hidden">: {{ label() }}</span>
    </button>
    @if (!reader.supported()) {
      <span class="visually-hidden" [id]="hintId()">
        Read aloud isn't available in this browser: it needs an on-device voice.
      </span>
    }
  `,
  styles: `
    :host {
      display: inline-block;
    }
    .read-aloud {
      min-height: 44px;
    }
  `,
})
export class ReadAloudButton {
  /** A key unique on the page, so one card at a time shows as speaking. */
  readonly id = input.required<string>();
  readonly text = input.required<string>();
  readonly label = input.required<string>();

  protected readonly reader = inject(ReadAloudService);
  protected readonly on = computed(() => this.reader.speaking() === this.id());
  protected readonly hintId = computed(() => `read-aloud-hint-${this.id()}`);
}
