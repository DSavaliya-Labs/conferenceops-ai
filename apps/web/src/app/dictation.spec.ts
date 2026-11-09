import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { App } from './app';
import { routes } from './app.routes';
import { DictationButton } from './dictation-button';

const SPOKEN = 'kubernetes security for beginners';

/** MediaRecorder as the component uses it: start, stop (one chunk, then the stop event), state and mimeType. */
class FakeMediaRecorder extends EventTarget {
  static instances: FakeMediaRecorder[] = [];

  static isTypeSupported(type: string): boolean {
    return type === 'audio/webm;codecs=opus';
  }

  state: 'inactive' | 'recording' = 'inactive';
  readonly mimeType: string;

  constructor(
    readonly stream: MediaStream,
    options?: MediaRecorderOptions,
  ) {
    super();
    this.mimeType = options?.mimeType ?? '';
    FakeMediaRecorder.instances.push(this);
  }

  start(): void {
    this.state = 'recording';
  }

  stop(): void {
    this.state = 'inactive';
    const chunk = Object.assign(new Event('dataavailable'), {
      data: new Blob(['opus bytes'], { type: 'audio/webm' }),
    });
    this.dispatchEvent(chunk);
    this.dispatchEvent(new Event('stop'));
  }
}

function fakeMicrophone() {
  const track = { stop: vi.fn() };
  const stream = { getTracks: () => [track] } as unknown as MediaStream;
  return { track, stream };
}

function installMediaApis(
  getUserMedia: (constraints: MediaStreamConstraints) => Promise<MediaStream>,
): void {
  Object.defineProperty(navigator, 'mediaDevices', { value: { getUserMedia }, configurable: true });
  vi.stubGlobal('MediaRecorder', FakeMediaRecorder);
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

function render() {
  TestBed.configureTestingModule({
    imports: [App],
    providers: [provideRouter(routes), provideHttpClient(), provideHttpClientTesting()],
  });
  const fixture = TestBed.createComponent(App);
  fixture.detectChanges();
  return {
    fixture,
    el: fixture.nativeElement as HTMLElement,
    http: TestBed.inject(HttpTestingController),
  };
}

async function click(
  fixture: ComponentFixture<unknown>,
  el: HTMLElement,
  selector: string,
): Promise<void> {
  const button = el.querySelector<HTMLButtonElement>(selector);
  expect(button, selector).not.toBeNull();
  button!.click();
  await settle();
  fixture.detectChanges();
}

function status(el: HTMLElement): string {
  return el.querySelector('#dictation-status')?.textContent?.trim() ?? '';
}

function askInput(el: HTMLElement): HTMLInputElement {
  return el.querySelector<HTMLInputElement>('#ask-input')!;
}

async function typeAndSearch(
  fixture: ComponentFixture<unknown>,
  el: HTMLElement,
  text: string,
): Promise<void> {
  const input = askInput(el);
  input.value = text;
  input.dispatchEvent(new Event('input'));
  el.querySelector('form[role="search"]')!.dispatchEvent(new Event('submit', { cancelable: true }));
  fixture.detectChanges();
  await fixture.whenStable();
}

afterEach(() => {
  vi.unstubAllGlobals();
  delete (navigator as { mediaDevices?: unknown }).mediaDevices;
  FakeMediaRecorder.instances = [];
});

describe('Dictation (the spoken half of the Ask bar)', () => {
  it('is a labeled button in the Ask row that never submits the form (A11Y-1)', () => {
    const { el } = render();
    const mic = el.querySelector<HTMLButtonElement>('form[role="search"] .field button.mic')!;
    expect(mic.textContent).toContain('Dictate');
    expect(mic.getAttribute('type')).toBe('button');
    expect(mic.disabled).toBe(false);
    const live = el.querySelector('#dictation-status')!;
    expect(live.getAttribute('aria-live')).toBe('polite');
    expect(live.getAttribute('aria-atomic')).toBe('true');
    expect(mic.getAttribute('aria-describedby')).toBe('dictation-status');
    expect(live.textContent?.trim()).toBe(''); // quiet until something happens
  });

  it('says so when the browser cannot record, and typing still works', async () => {
    const { fixture, el, http } = render();
    await click(fixture, el, 'button.mic');
    expect(status(el)).toContain("can't record audio");
    expect(status(el)).toContain('You can still type your question.');
    http.expectNone('/api/v1/dictation');

    await typeAndSearch(fixture, el, 'quiet rooms');
    expect(http.expectOne('/api/v1/search').request.body).toEqual({ query: 'quiet rooms' });
  });

  it('says so when microphone permission is denied, and keeps what was typed', async () => {
    installMediaApis(() =>
      Promise.reject(new DOMException('Permission denied', 'NotAllowedError')),
    );
    const { fixture, el, http } = render();
    askInput(el).value = 'service mesh';
    askInput(el).dispatchEvent(new Event('input'));

    await click(fixture, el, 'button.mic');

    expect(status(el)).toContain('Microphone access was denied');
    expect(askInput(el).value).toBe('service mesh');
    expect(askInput(el).disabled).toBe(false);
    expect(FakeMediaRecorder.instances).toHaveLength(0);
    http.expectNone('/api/v1/dictation');
  });

  it('records, sends the recording as the body, and puts the text in the Ask bar for review without searching', async () => {
    const { track, stream } = fakeMicrophone();
    const getUserMedia = vi.fn(() => Promise.resolve(stream));
    installMediaApis(getUserMedia);
    const { fixture, el, http } = render();

    await click(fixture, el, 'button.mic');
    expect(getUserMedia).toHaveBeenCalledWith({ audio: true });
    const [recorder] = FakeMediaRecorder.instances;
    expect(recorder.state).toBe('recording');
    expect(recorder.mimeType).toBe('audio/webm;codecs=opus');
    const mic = el.querySelector<HTMLButtonElement>('button.mic')!;
    expect(mic.textContent).toContain('Stop recording'); // the same button is the stop control
    expect(el.querySelector('button.cancel')).not.toBeNull();
    expect(status(el)).toContain('Recording');

    await click(fixture, el, 'button.mic');
    expect(recorder.state).toBe('inactive');
    expect(track.stop).toHaveBeenCalled(); // the microphone is released before the upload
    expect(status(el)).toContain('Transcribing');
    expect(mic.getAttribute('aria-disabled')).toBe('true');
    const req = http.expectOne('/api/v1/dictation');
    expect(req.request.method).toBe('POST');
    expect(req.request.urlWithParams).toBe('/api/v1/dictation');
    expect(req.request.body).toBeInstanceOf(Blob);
    expect(req.request.headers.get('Content-Type')).toBe('audio/webm;codecs=opus');

    req.flush({ text: `  ${SPOKEN} ` });
    fixture.detectChanges();
    expect(askInput(el).value).toBe(SPOKEN);
    expect(document.activeElement).toBe(askInput(el)); // ready to review and edit
    expect(status(el)).toContain('Review the text');
    expect(mic.textContent).toContain('Dictate');
    http.expectNone('/api/v1/search'); // nothing is submitted until the attendee presses Search
  });

  it('ignores repeated Dictate clicks while microphone access is pending', async () => {
    let resolveMicrophone!: (stream: MediaStream) => void;
    const pendingMicrophone = new Promise<MediaStream>((resolve) => (resolveMicrophone = resolve));
    const getUserMedia = vi.fn(() => pendingMicrophone);
    installMediaApis(getUserMedia);
    const { fixture, el } = render();
    const mic = el.querySelector<HTMLButtonElement>('button.mic')!;

    mic.click();
    mic.click();
    expect(getUserMedia).toHaveBeenCalledTimes(1);

    const { track, stream } = fakeMicrophone();
    resolveMicrophone(stream);
    await settle();
    fixture.detectChanges();

    expect(FakeMediaRecorder.instances).toHaveLength(1);
    expect(FakeMediaRecorder.instances[0].state).toBe('recording');
    fixture.destroy();
    expect(track.stop).toHaveBeenCalled();
  });

  it('releases a microphone stream that arrives after the component is destroyed', async () => {
    let resolveMicrophone!: (stream: MediaStream) => void;
    const pendingMicrophone = new Promise<MediaStream>((resolve) => (resolveMicrophone = resolve));
    installMediaApis(() => pendingMicrophone);
    const { fixture, el } = render();

    el.querySelector<HTMLButtonElement>('button.mic')!.click();
    fixture.destroy();
    const { track, stream } = fakeMicrophone();
    resolveMicrophone(stream);
    await settle();

    expect(FakeMediaRecorder.instances).toHaveLength(0);
    expect(track.stop).toHaveBeenCalled();
  });

  it('cancel throws the recording away: nothing is sent, focus returns to the button', async () => {
    const { track, stream } = fakeMicrophone();
    installMediaApis(() => Promise.resolve(stream));
    const { fixture, el, http } = render();

    await click(fixture, el, 'button.mic');
    await click(fixture, el, 'button.cancel');

    expect(FakeMediaRecorder.instances[0].state).toBe('inactive');
    expect(track.stop).toHaveBeenCalled();
    expect(status(el)).toContain('Cancelled');
    expect(el.querySelector('button.cancel')).toBeNull();
    expect(document.activeElement).toBe(el.querySelector('button.mic'));
    http.expectNone('/api/v1/dictation');
  });

  it('a failed transcription says so and leaves the typed text alone', async () => {
    installMediaApis(() => Promise.resolve(fakeMicrophone().stream));
    const { fixture, el, http } = render();
    askInput(el).value = 'platform engineering';
    askInput(el).dispatchEvent(new Event('input'));

    await click(fixture, el, 'button.mic');
    await click(fixture, el, 'button.mic');
    http
      .expectOne('/api/v1/dictation')
      .flush('down', { status: 503, statusText: 'Service Unavailable' });
    fixture.detectChanges();

    expect(status(el)).toContain("Transcription isn't available right now");
    expect(askInput(el).value).toBe('platform engineering');
    expect(el.querySelector('button.mic')?.textContent).toContain('Dictate'); // ready to try again
  });

  it('an empty transcript asks for another try instead of filling the Ask bar with nothing', async () => {
    installMediaApis(() => Promise.resolve(fakeMicrophone().stream));
    const { fixture, el, http } = render();

    await click(fixture, el, 'button.mic');
    await click(fixture, el, 'button.mic');
    http.expectOne('/api/v1/dictation').flush({ text: '   ' });
    fixture.detectChanges();

    expect(status(el)).toContain('Nothing was recognized');
    expect(askInput(el).value).toBe('');
  });

  it('stops itself at the app limit and transcribes what it has (PLAN §9.2)', async () => {
    installMediaApis(() => Promise.resolve(fakeMicrophone().stream));
    TestBed.configureTestingModule({
      imports: [DictationButton],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    const fixture = TestBed.createComponent(DictationButton);
    fixture.componentRef.setInput('maxSeconds', 0.01);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    const http = TestBed.inject(HttpTestingController);

    await click(fixture, el, 'button.mic');
    await new Promise((resolve) => setTimeout(resolve, 40));
    fixture.detectChanges();

    expect(FakeMediaRecorder.instances[0].state).toBe('inactive');
    expect(http.expectOne('/api/v1/dictation').request.method).toBe('POST');
  });
});
