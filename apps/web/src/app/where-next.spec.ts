import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { App } from './app';
import { Persona } from './personas';
import { WhereNextResponse } from './where-next';

const ACCESS_PAGE =
  'https://events.linuxfoundation.org/kubecon-cloudnativecon-north-america/attend/inclusion-accessibility/';
const STEP_FREE =
  "Step-free routes aren't published for this event. For help locating an elevator or other accessibility questions, " +
  'ask Linux Foundation staff at the registration or information desks.';

const PERSONAS: Persona[] = [
  {
    id: 'p-aisha',
    displayName: 'Aisha Rahman',
    role: 'attendee',
    interests: ['AI SRE'],
    isFictional: true,
  },
  {
    id: 'p-tomas',
    displayName: 'Tomás Vega',
    role: 'attendee',
    interests: ['GitOps'],
    isFictional: true,
  },
];

function answer(overrides: Partial<WhereNextResponse> = {}): WhereNextResponse {
  return {
    requestId: 'r1',
    status: 'matches',
    retrievalMode: 'hybrid',
    conferenceTime: '2026-11-10T00:30:00Z',
    conferenceTimeVenue: 'Mon Nov 9, 5:30 PM MST',
    observedAt: '2026-11-10T00:30:00Z',
    window: 'today',
    candidates: [
      {
        sessionId: 's1',
        title: 'The SRE Is Not Dead',
        event: 'KubeCon + CloudNativeCon North America 2026',
        conference: 'kubecon-na-2026',
        isFictional: false,
        startsAt: '2026-11-10T22:40:00Z',
        endsAt: '2026-11-10T23:10:00Z',
        startsAtVenue: 'Tue Nov 10, 3:40 PM MST',
        room: 'Salt Palace | Level 1 | Grand Ballroom ACE',
        level: 'advanced',
        eligibility: 'unconfirmed',
        score: 0.84,
        sourceRevision: 2,
        reasons: [
          { code: 'INTEREST_MATCH', text: 'Mentions your interest: AI SRE', interests: ['AI SRE'] },
          {
            code: 'HISTORY_MATCH',
            text: 'Similar to “AI SRE in Practice”, which you attended at CloudBridge Summit 2026',
          },
        ],
        access: [
          {
            kind: 'captions',
            status: 'published',
            text: 'Captioning in 60+ languages through Wordly, with instructions in each breakout room.',
            sourceUrl: ACCESS_PAGE,
          },
          {
            kind: 'step_free_route',
            status: 'not_published',
            text: STEP_FREE,
            sourceUrl: ACCESS_PAGE,
          },
        ],
        sourceIds: ['session:s1:2'],
      },
    ],
    excluded: { STARTED: 2 },
    warnings: [{ code: 'STEP_FREE_NOT_PUBLISHED', text: STEP_FREE, sourceUrl: ACCESS_PAGE }],
    ...overrides,
  };
}

function render() {
  TestBed.configureTestingModule({
    imports: [App],
    providers: [provideHttpClient(), provideHttpClientTesting()],
  });
  const fixture = TestBed.createComponent(App);
  fixture.detectChanges();
  const http = TestBed.inject(HttpTestingController);
  return { fixture, el: fixture.nativeElement as HTMLElement, http };
}

async function settle(fixture: ReturnType<typeof render>['fixture']) {
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();
}

async function loadPersonas(view: ReturnType<typeof render>) {
  view.http.expectOne('/api/v1/demo/profiles').flush({ personas: PERSONAS });
  await settle(view.fixture);
}

async function choose(view: ReturnType<typeof render>, id: string) {
  const select = view.el.querySelector<HTMLSelectElement>('#persona-select')!;
  select.value = id;
  select.dispatchEvent(new Event('change'));
  await settle(view.fixture);
}

async function askWhereNext(view: ReturnType<typeof render>) {
  view.el.querySelector<HTMLFormElement>('form.where-next')!.dispatchEvent(new Event('submit'));
  await settle(view.fixture);
}

describe('Persona switcher', () => {
  it('is a labeled native select listing the demo personas as fictional (A11Y-1)', async () => {
    const view = render();
    expect(view.el.querySelector('label[for="persona-select"]')?.textContent).toContain(
      'Viewing as',
    );
    await loadPersonas(view);
    const select = view.el.querySelector<HTMLSelectElement>('#persona-select')!;
    expect(select.disabled).toBe(false);
    const options = Array.from(select.options).map((o) => o.textContent?.trim());
    expect(options).toEqual([
      'Choose a demo persona',
      'Aisha Rahman · attendee · fictional',
      'Tomás Vega · attendee · fictional',
    ]);
    expect(view.el.querySelector('#persona-hint')?.textContent).toContain('not a sign-in');
  });

  it('says so when the personas are unavailable', async () => {
    const view = render();
    view.http
      .expectOne('/api/v1/demo/profiles')
      .flush('down', { status: 503, statusText: 'Service Unavailable' });
    await settle(view.fixture);
    expect(view.el.querySelector<HTMLSelectElement>('#persona-select')!.disabled).toBe(true);
    expect(view.el.querySelector('#persona-hint')?.textContent).toContain("aren't available");
  });
});

describe('Where should I go next?', () => {
  it('asks for a persona first instead of sending a request', async () => {
    const view = render();
    await loadPersonas(view);
    await askWhereNext(view);
    view.http.expectNone('/api/v1/recommendations/next');
    expect(view.el.querySelector('form.where-next [role="alert"]')?.textContent).toContain(
      'Choose a demo persona first',
    );
  });

  it('sends the persona in the header, nothing personal in the URL, and the chosen window', async () => {
    const view = render();
    await loadPersonas(view);
    await choose(view, 'p-aisha');
    const when = view.el.querySelector<HTMLSelectElement>('#where-next-window')!;
    when.value = 'tomorrow_morning';
    when.dispatchEvent(new Event('change'));
    await askWhereNext(view);
    const req = view.http.expectOne('/api/v1/recommendations/next');
    expect(req.request.method).toBe('POST');
    expect(req.request.urlWithParams).toBe('/api/v1/recommendations/next');
    expect(req.request.headers.get('X-Demo-Profile-ID')).toBe('p-aisha');
    expect(req.request.body).toEqual({ window: 'tomorrow_morning', limit: 3 });
  });

  it('renders cards with title, venue time, room, reasons, sourced access facts and a real-event label', async () => {
    const view = render();
    await loadPersonas(view);
    await choose(view, 'p-aisha');
    await askWhereNext(view);
    view.http.expectOne('/api/v1/recommendations/next').flush(answer());
    await settle(view.fixture);

    const card = view.el.querySelector('.card')!;
    expect(card.querySelector('h3')?.textContent).toContain('The SRE Is Not Dead');
    expect(card.querySelector('time')?.textContent).toContain('Tue Nov 10, 3:40 PM MST');
    expect(card.querySelector('time')?.getAttribute('datetime')).toBe('2026-11-10T22:40:00Z');
    expect(card.textContent).toContain('Salt Palace | Level 1 | Grand Ballroom ACE');
    expect(card.textContent).toContain('Real event, official schedule');
    const reasons = Array.from(card.querySelectorAll('.reasons li')).map((li) =>
      li.textContent?.trim(),
    );
    expect(reasons).toEqual([
      'Mentions your interest: AI SRE',
      'Similar to “AI SRE in Practice”, which you attended at CloudBridge Summit 2026',
    ]);
    const access = Array.from(card.querySelectorAll('.access li'));
    expect(access.length).toBe(2);
    for (const item of access) {
      expect(item.querySelector('a')?.getAttribute('href')).toBe(ACCESS_PAGE); // ACCESS-1: every fact has its link
    }
    expect(view.el.querySelector('[role="status"]')?.textContent).toContain(
      '1 suggestion for Aisha Rahman.',
    );
  });

  it('never labels a talk step-free: the only mention is that routes are not published', async () => {
    const view = render();
    await loadPersonas(view);
    await choose(view, 'p-aisha');
    await askWhereNext(view);
    view.http.expectOne('/api/v1/recommendations/next').flush(answer());
    await settle(view.fixture);
    const text = (view.el.querySelector('app-where-next-cards')?.textContent ?? '').toLowerCase();
    const mentions = text.split('step-free').length - 1;
    expect(mentions).toBeGreaterThan(0);
    expect(mentions).toBe(text.split("step-free routes aren't published").length - 1);
  });

  it('explains a no-fit answer in words, with the reasons counted', async () => {
    const view = render();
    await loadPersonas(view);
    await choose(view, 'p-tomas');
    await askWhereNext(view);
    view.http
      .expectOne('/api/v1/recommendations/next')
      .flush(
        answer({
          status: 'no_fit',
          candidates: [],
          warnings: [],
          excluded: { NOT_OPEN: 2, AGENDA_CONFLICT: 1, NO_MATCH: 4 },
        }),
      );
    await settle(view.fixture);
    const results = view.el.querySelector('app-where-next-cards')!;
    expect(results.querySelector('h2')?.textContent).toContain(
      'No talk fits for the rest of today',
    );
    const items = Array.from(results.querySelectorAll('.exclusions li')).map((li) =>
      li.textContent?.trim(),
    );
    expect(items).toEqual([
      "2 talks can't simply be walked into (off-site, sold out, or separate registration required)",
      '1 talk overlap a talk on your agenda',
      "4 talks don't match your interests or history",
    ]);
    expect(view.el.querySelector('[role="status"]')?.textContent).toContain(
      'No talk fits right now',
    );
  });

  it('switching persona cancels the request in flight and clears the last persona’s cards (PLAN §4.1)', async () => {
    const view = render();
    await loadPersonas(view);
    await choose(view, 'p-aisha');
    await askWhereNext(view);
    view.http.expectOne('/api/v1/recommendations/next').flush(answer());
    await settle(view.fixture);
    expect(view.el.querySelectorAll('.card').length).toBe(1);

    await askWhereNext(view);
    const inFlight = view.http.expectOne('/api/v1/recommendations/next');
    await choose(view, 'p-tomas');
    expect(inFlight.cancelled).toBe(true);
    expect(view.el.querySelectorAll('.card').length).toBe(0);
  });

  it('switching persona after an answer clears her cards, notices and status line (PLAN §4.1)', async () => {
    const view = render();
    await loadPersonas(view);
    await choose(view, 'p-aisha');
    await askWhereNext(view);
    view.http.expectOne('/api/v1/recommendations/next').flush(answer());
    await settle(view.fixture);
    // Aisha's answer is on screen, with her access-need notice, and nothing is in flight.
    view.http.verify();
    expect(view.el.querySelectorAll('.card').length).toBe(1);
    expect(view.el.querySelector('app-where-next-cards')?.textContent).toContain(STEP_FREE);
    expect(view.el.querySelector('[role="status"]')?.textContent).toContain(
      '1 suggestion for Aisha Rahman.',
    );

    await choose(view, 'p-tomas');
    expect(view.el.querySelectorAll('.card').length).toBe(0);
    expect(view.el.querySelector('app-where-next-cards')?.textContent?.trim()).toBe('');
    expect(view.el.querySelector('[role="status"]')?.textContent?.trim()).toBe('');
    const button = view.el.querySelector<HTMLButtonElement>('#where-next-button')!;
    expect(button.textContent?.trim()).toBe('Where should I go next?');
    expect(button.disabled).toBe(false);
    view.http.expectNone('/api/v1/recommendations/next');
  });

  it('shows a friendly message when suggestions are down', async () => {
    const view = render();
    await loadPersonas(view);
    await choose(view, 'p-aisha');
    await askWhereNext(view);
    view.http
      .expectOne('/api/v1/recommendations/next')
      .flush('down', { status: 503, statusText: 'Service Unavailable' });
    await settle(view.fixture);
    expect(view.el.textContent).toContain("Suggestions aren't available right now");
  });
});
