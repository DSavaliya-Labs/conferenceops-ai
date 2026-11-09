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

type LocalVoice = { localService: boolean; lang: string };

type Synth = {
  speak(utterance: unknown): void;
  cancel(): void;
  getVoices(): LocalVoice[];
};

/** Reads a card aloud with the browser's own speech synthesis (Web Speech API): the text never leaves the device,
 *  nothing is recorded, and one card speaks at a time. Unsupported browsers get a disabled button that says so. */
@Service()
export class ReadAloudService {
  readonly supported = typeof window !== 'undefined' && 'speechSynthesis' in window;
  /** The card being read, or null. */
  readonly speaking = signal<string | null>(null);

  private get synth(): Synth | null {
    return this.supported
      ? (window as unknown as { speechSynthesis: Synth }).speechSynthesis
      : null;
  }

  toggle(id: string, text: string): void {
    const synth = this.synth;
    if (!synth) {
      return;
    }
    if (this.speaking() === id) {
      this.stop();
      return;
    }
    const voices = synth.getVoices();
    const local =
      voices.find((voice) => voice.localService && voice.lang === 'en-US') ??
      voices.find((voice) => voice.localService);
    if (!local) {
      return; // no on-device voice: don't send text to a network voice (PRIV-1)
    }
    synth.cancel();
    const Utterance = (
      window as unknown as { SpeechSynthesisUtterance: new (t: string) => UtteranceLike }
    ).SpeechSynthesisUtterance;
    const utterance = new Utterance(text);
    utterance.lang = 'en-US';
    utterance.voice = local;
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

interface UtteranceLike {
  lang: string;
  voice: LocalVoice | null;
  onend: (() => void) | null;
  onerror: (() => void) | null;
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
      [attr.aria-describedby]="reader.supported ? null : hintId()"
      [disabled]="!reader.supported"
      (click)="reader.toggle(id(), text())"
    >
      <app-icon [name]="on() ? 'close' : 'activity'" [size]="16" />
      {{ on() ? 'Stop reading' : 'Read aloud' }}
      <span class="visually-hidden">: {{ label() }}</span>
    </button>
    @if (!reader.supported) {
      <span class="visually-hidden" [id]="hintId()">
        Read aloud isn't available in this browser.
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
