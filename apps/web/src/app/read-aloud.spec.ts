import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { ReadAloudButton, ReadAloudService, sessionSpeech } from './read-aloud';
import { text } from './test-support';

class FakeUtterance {
  lang = '';
  onend: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(readonly text: string) {}
}

function fakeSynth() {
  const spoken: FakeUtterance[] = [];
  let cancels = 0;
  (window as unknown as { SpeechSynthesisUtterance: unknown }).SpeechSynthesisUtterance =
    FakeUtterance;
  (window as unknown as { speechSynthesis: unknown }).speechSynthesis = {
    speak: (u: FakeUtterance) => spoken.push(u),
    cancel: () => {
      cancels += 1;
    },
  };
  return { spoken, cancels: () => cancels };
}

@Component({
  imports: [ReadAloudButton],
  template: `
    <app-read-aloud-button id="a" [text]="first()" label="the first talk" />
    <app-read-aloud-button id="b" [text]="second()" label="the second talk" />
  `,
})
class Host {
  readonly first = signal('First talk. Tuesday 10:30.');
  readonly second = signal('Second talk. Wednesday 11:00.');
}

function render() {
  TestBed.configureTestingModule({ imports: [Host] });
  const fixture = TestBed.createComponent(Host);
  fixture.detectChanges();
  const buttons = () =>
    fixture.nativeElement.querySelectorAll('button') as NodeListOf<HTMLButtonElement>;
  return { fixture, buttons };
}

describe('Read aloud', () => {
  const original = {
    synth: (window as unknown as { speechSynthesis: unknown }).speechSynthesis,
    utterance: (window as unknown as { SpeechSynthesisUtterance: unknown })
      .SpeechSynthesisUtterance,
  };
  afterEach(() => {
    (window as unknown as { speechSynthesis: unknown }).speechSynthesis = original.synth;
    (window as unknown as { SpeechSynthesisUtterance: unknown }).SpeechSynthesisUtterance =
      original.utterance;
  });

  it('speaks the card with the browser, names what it reads, and stops on the second press', () => {
    const synth = fakeSynth();
    const { fixture, buttons } = render();
    expect(text(buttons()[0])).toContain('Read aloud');
    expect(text(buttons()[0])).toContain('the first talk');
    buttons()[0].click();
    fixture.detectChanges();
    expect(synth.spoken.map((u) => u.text)).toEqual(['First talk. Tuesday 10:30.']);
    expect(synth.spoken[0].lang).toBe('en-US');
    expect(buttons()[0].getAttribute('aria-pressed')).toBe('true');
    expect(text(buttons()[0])).toContain('Stop reading');
    buttons()[0].click();
    fixture.detectChanges();
    expect(synth.cancels()).toBeGreaterThan(0);
    expect(buttons()[0].getAttribute('aria-pressed')).toBe('false');
  });

  it('reads one card at a time: starting another cancels the first', () => {
    const synth = fakeSynth();
    const { fixture, buttons } = render();
    buttons()[0].click();
    fixture.detectChanges();
    buttons()[1].click();
    fixture.detectChanges();
    expect(synth.spoken.map((u) => u.text)).toEqual([
      'First talk. Tuesday 10:30.',
      'Second talk. Wednesday 11:00.',
    ]);
    expect(Array.from(buttons()).map((b) => b.getAttribute('aria-pressed'))).toEqual([
      'false',
      'true',
    ]);
    synth.spoken[1].onend?.();
    fixture.detectChanges();
    expect(buttons()[1].getAttribute('aria-pressed')).toBe('false');
  });

  it('is disabled with an explanation when the browser has no speech synthesis', () => {
    delete (window as unknown as { speechSynthesis?: unknown }).speechSynthesis;
    const { buttons, fixture } = render();
    expect(buttons()[0].disabled).toBe(true);
    const hint = fixture.nativeElement.querySelector(
      `#${buttons()[0].getAttribute('aria-describedby')}`,
    );
    expect(text(hint)).toContain("Read aloud isn't available in this browser");
    expect(TestBed.inject(ReadAloudService).supported).toBe(false);
  });

  it('reads a talk card in order: title, event, venue time, room, level and topics', () => {
    expect(
      sessionSpeech({
        id: 's1',
        title: 'Kernel Eyes',
        event: 'CloudBridge Summit 2026',
        isFictional: true,
        startsAt: '2026-10-06T16:00:00Z',
        startsAtVenue: 'Tue Oct 6, 10:00 AM MDT',
        room: null,
        level: 'advanced',
        topics: ['security', 'eBPF'],
      }),
    ).toBe(
      'Kernel Eyes. CloudBridge Summit 2026. Tue Oct 6, 10:00 AM MDT. Room: not listed in the schedule. Level: advanced. Topics: security, eBPF.',
    );
  });
});
