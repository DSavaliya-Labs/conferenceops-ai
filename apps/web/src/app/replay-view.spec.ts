import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap } from '@angular/router';
import { of } from 'rxjs';

import { ReplayView } from './replay';
import { ReplayViewComponent, isTrustedMediaPath } from './replay-view';

const SESSION_ID = '2f1c8f7e-4f1a-4a51-9b1a-0c7a5b2d9e11';

function view(status: 'processing' | 'published'): ReplayView {
  const published = status === 'published';
  return {
    session: {
      id: SESSION_ID,
      title: 'Tracing LLM Calls in Production',
      speakers: ['Ananya Iyer'],
      event: 'CloudBridge Summit 2026',
      conference: 'cloudbridge-summit-2026',
      isFictional: true,
      startsAtVenue: 'Thu Oct 8, 10:00 AM MDT',
    },
    media: {
      url: '/media/cloudbridge/clip-3.mp4',
      captionsUrl: '/media/cloudbridge/clip-3.vtt',
      durationSeconds: 71,
      label: 'Simulated excerpt',
    },
    transcript: {
      status,
      revision: 1,
      label: 'Simulated excerpt',
      cues: published
        ? [
            {
              start: 1.5,
              end: 6.7,
              text: 'A language model call is the slowest dependency most of us have added.',
            },
            { start: 6.7, end: 11.6, text: 'So trace it like one.' },
            { start: 11.6, end: 15.0, text: 'What we never record is the prompt.' },
          ]
        : [],
      text: published
        ? 'A language model call is the slowest dependency most of us have added. So trace it like one.'
        : null,
    },
  };
}

async function render(startAt?: string) {
  TestBed.configureTestingModule({
    imports: [ReplayViewComponent],
    providers: [
      provideHttpClient(),
      provideHttpClientTesting(),
      {
        provide: ActivatedRoute,
        useValue: {
          paramMap: of(convertToParamMap({ id: SESSION_ID })),
          queryParamMap: of(convertToParamMap(startAt ? { t: startAt } : {})),
        },
      },
    ],
  });
  const fixture = TestBed.createComponent(ReplayViewComponent);
  fixture.detectChanges();
  await fixture.whenStable();
  const http = TestBed.inject(HttpTestingController);
  return { fixture, el: fixture.nativeElement as HTMLElement, http };
}

async function flush(
  fixture: Awaited<ReturnType<typeof render>>['fixture'],
  http: HttpTestingController,
  body: ReplayView,
) {
  http.expectOne(`/api/v1/sessions/${SESSION_ID}/replay`).flush(body);
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();
}

describe('ReplayViewComponent', () => {
  it('shows the video with captions on, the labels, the pause control and the transcript panel (A11Y-1, SPEAKER-1)', async () => {
    const { fixture, el, http } = await render();
    await flush(fixture, http, view('published'));

    const video = el.querySelector<HTMLVideoElement>('video.player')!;
    expect(video.getAttribute('src')).toBe('/media/cloudbridge/clip-3.mp4');
    expect(video.hasAttribute('controls')).toBe(true);
    const track = video.querySelector('track')!;
    expect(track.getAttribute('kind')).toBe('captions');
    expect(track.getAttribute('src')).toBe('/media/cloudbridge/clip-3.vtt');
    expect(track.hasAttribute('default')).toBe(true);

    const text = el.textContent ?? '';
    expect(text).toContain('Fictional demo event');
    expect(text).toContain('Simulated excerpt');
    expect(text).toContain('Tracing LLM Calls in Production');
    expect(el.querySelector('.controls button')?.textContent?.trim()).toBe('Play');

    const cues = el.querySelectorAll('.cues li');
    expect(cues.length).toBe(3);
    expect(cues[2].textContent).toContain('0:11');
    expect(cues[2].textContent).toContain('What we never record is the prompt.');
    // The transcript panel is never a live region.
    expect(el.querySelector('.transcript [aria-live]')).toBeNull();
    expect(el.querySelector('[role="log"]')).toBeNull();
    // Focus lands on the replay's heading, not at the top of the page.
    expect(document.activeElement).toBe(el.querySelector('#replay-heading'));
  });

  it('opens at the cited moment (?t=) once the player knows its duration, and a cue seeks the replay', async () => {
    const { fixture, el, http } = await render('11.6');
    await flush(fixture, http, view('published'));
    const video = el.querySelector<HTMLVideoElement>('video.player')!;
    Object.defineProperty(video, 'readyState', { value: 1, configurable: true });

    video.dispatchEvent(new Event('loadedmetadata'));
    expect(video.currentTime).toBeCloseTo(11.6);

    (el.querySelectorAll<HTMLButtonElement>('.cues button')[1] as HTMLButtonElement).click();
    expect(video.currentTime).toBeCloseTo(6.7);
  });

  it('highlights the line being spoken, and "Pause updates" keeps the panel still while the replay plays', async () => {
    const { fixture, el, http } = await render();
    await flush(fixture, http, view('published'));
    const video = el.querySelector<HTMLVideoElement>('video.player')!;

    video.currentTime = 7;
    video.dispatchEvent(new Event('timeupdate'));
    fixture.detectChanges();
    expect(el.querySelector('.cues li[aria-current="true"]')?.textContent).toContain(
      'So trace it like one.',
    );

    const pause = el.querySelector<HTMLButtonElement>('.panel-controls button')!;
    expect(pause.textContent?.trim()).toBe('Pause updates');
    pause.click();
    fixture.detectChanges();
    expect(pause.textContent?.trim()).toBe('Resume updates');
    expect(pause.getAttribute('aria-pressed')).toBe('false');
    video.currentTime = 12;
    video.dispatchEvent(new Event('timeupdate'));
    fixture.detectChanges();
    expect(el.querySelector('.cues li[aria-current="true"]')?.textContent).toContain(
      'So trace it like one.',
    ); // unchanged
  });

  it('says a processing transcript is not searchable and shows none of its text, while the replay still plays', async () => {
    const { fixture, el, http } = await render();
    await flush(fixture, http, view('processing'));

    const text = el.textContent ?? '';
    expect(text).toContain('Transcript processing');
    expect(text).toContain("isn't searchable");
    expect(el.querySelector('.cues')).toBeNull();
    expect(text).not.toContain('language model call');
    expect(el.querySelector('video.player')?.getAttribute('src')).toBe(
      '/media/cloudbridge/clip-3.mp4',
    );
    expect(el.querySelector('video track')).not.toBeNull();
  });

  it('tells the attendee when a session has no replay, without an error page', async () => {
    const { fixture, el, http } = await render();
    http
      .expectOne(`/api/v1/sessions/${SESSION_ID}/replay`)
      .flush('none', { status: 404, statusText: 'Not Found' });
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(el.querySelector('[role="alert"]')?.textContent).toContain('This session has no replay');
    expect(el.querySelector('video')).toBeNull();
  });

  it('only plays media served by this site under /media/ (SEC-1)', () => {
    expect(isTrustedMediaPath('/media/cloudbridge/clip-1.mp4')).toBe(true);
    expect(isTrustedMediaPath('https://evil.example/clip.mp4')).toBe(false);
    expect(isTrustedMediaPath('//evil.example/clip.mp4')).toBe(false);
    expect(isTrustedMediaPath('/media/../config.json')).toBe(false);
  });
});
