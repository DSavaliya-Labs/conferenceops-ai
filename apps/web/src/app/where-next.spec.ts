import { WhereNextResponse } from './where-next';
import {
  AISHA,
  View,
  choosePersona,
  click,
  loadPersonas,
  navigate,
  renderApp,
  settle,
  text,
  texts,
} from './test-support';

type AppView = View<import('./app').App>;

const ACCESS_PAGE =
  'https://events.linuxfoundation.org/kubecon-cloudnativecon-north-america/attend/inclusion-accessibility/';
const STEP_FREE =
  "Step-free routes aren't published for this event. For help locating an elevator or other accessibility questions, " +
  'ask Linux Foundation staff at the registration or information desks.';

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

async function start(): Promise<AppView> {
  const view = renderApp();
  await loadPersonas(view);
  await navigate(view, '/where-next');
  return view;
}

async function askWhereNext(view: AppView): Promise<void> {
  view.el
    .querySelector<HTMLFormElement>('form.where-next')!
    .dispatchEvent(new Event('submit', { cancelable: true }));
  await settle(view);
}

describe('Where should I go next?', () => {
  it('has the page, the When control and the button; it asks for a persona first instead of sending a request', async () => {
    const view = await start();
    expect(text(view.el.querySelector('h1'))).toBe('Where should I go next?');
    expect(view.el.querySelector('label[for="where-next-window"]')).not.toBeNull();
    expect(text(view.el.querySelector('#where-next-hint'))).toContain('Choose a demo persona');
    await askWhereNext(view);
    view.http.expectNone('/api/v1/recommendations/next');
    expect(text(view.el.querySelector('form.where-next [role="alert"]'))).toContain(
      'Choose a demo persona first',
    );
  });

  it('sends the persona in the header, nothing personal in the URL, and the chosen window', async () => {
    const view = await start();
    await choosePersona(view, 'Aisha Rahman');
    expect(text(view.el.querySelector('#where-next-hint'))).toContain('For Aisha Rahman');
    const when = view.el.querySelector<HTMLSelectElement>('#where-next-window')!;
    when.value = 'tomorrow_morning';
    when.dispatchEvent(new Event('change'));
    await askWhereNext(view);
    const req = view.http.expectOne('/api/v1/recommendations/next');
    expect(req.request.method).toBe('POST');
    expect(req.request.urlWithParams).toBe('/api/v1/recommendations/next');
    expect(req.request.headers.get('X-Demo-Profile-ID')).toBe(AISHA.id);
    expect(req.request.body).toEqual({ window: 'tomorrow_morning', limit: 3 });
    expect(text(view.el.querySelector('[role="status"]'))).toContain(
      'Finding talks for Aisha Rahman',
    );
  });

  it('renders cards with title, venue time, room, reasons, sourced access facts and a real-event label', async () => {
    const view = await start();
    await choosePersona(view, 'Aisha Rahman');
    await askWhereNext(view);
    view.http.expectOne('/api/v1/recommendations/next').flush(answer());
    await settle(view);

    const card = view.el.querySelector('app-where-next-cards .card')!;
    expect(text(card.querySelector('h3'))).toBe('The SRE Is Not Dead');
    expect(text(card.querySelector('time'))).toBe('Tue Nov 10, 3:40 PM MST');
    expect(card.querySelector('time')?.getAttribute('datetime')).toBe('2026-11-10T22:40:00Z');
    expect(text(card)).toContain('Salt Palace | Level 1 | Grand Ballroom ACE');
    expect(text(card.querySelector('.badge'))).toBe('Real event, official schedule');
    expect(texts(card, '.reasons li')).toEqual([
      'Mentions your interest: AI SRE',
      'Similar to “AI SRE in Practice”, which you attended at CloudBridge Summit 2026',
    ]);
    const access = Array.from(card.querySelectorAll('.access li'));
    expect(access.length).toBe(2);
    for (const item of access) {
      expect(item.querySelector('a')?.getAttribute('href')).toBe(ACCESS_PAGE); // ACCESS-1: every fact has its link
    }
    expect(text(view.el.querySelector('[role="status"]'))).toContain(
      '1 suggestion for Aisha Rahman.',
    );
  });

  it('never labels a talk step-free: the only mention is that routes are not published', async () => {
    const view = await start();
    await choosePersona(view, 'Aisha Rahman');
    await askWhereNext(view);
    view.http.expectOne('/api/v1/recommendations/next').flush(answer());
    await settle(view);
    const words = (view.el.querySelector('app-where-next-cards')?.textContent ?? '').toLowerCase();
    const mentions = words.split('step-free').length - 1;
    expect(mentions).toBeGreaterThan(0);
    expect(mentions).toBe(words.split("step-free routes aren't published").length - 1);
  });

  it('explains a no-fit answer in words, with the reasons counted', async () => {
    const view = await start();
    await choosePersona(view, 'Tomás Vega');
    await askWhereNext(view);
    view.http.expectOne('/api/v1/recommendations/next').flush(
      answer({
        status: 'no_fit',
        candidates: [],
        warnings: [],
        excluded: { NOT_OPEN: 2, AGENDA_CONFLICT: 1, NO_MATCH: 4 },
      }),
    );
    await settle(view);
    const results = view.el.querySelector('app-where-next-cards')!;
    expect(text(results.querySelector('h2'))).toContain('No talk fits for the rest of today');
    expect(texts(results, '.exclusions li')).toEqual([
      "2 talks can't simply be walked into (off-site, sold out, or separate registration required)",
      '1 talk overlap a talk on your agenda',
      "4 talks don't match your interests or history",
    ]);
    expect(text(view.el.querySelector('[role="status"]'))).toContain('No talk fits right now');
  });

  it('switching persona cancels the request in flight and clears the last persona’s cards (PLAN §4.1)', async () => {
    const view = await start();
    await choosePersona(view, 'Aisha Rahman');
    await askWhereNext(view);
    view.http.expectOne('/api/v1/recommendations/next').flush(answer());
    await settle(view);
    expect(view.el.querySelectorAll('app-where-next-cards .card').length).toBe(1);

    await askWhereNext(view);
    const inFlight = view.http.expectOne('/api/v1/recommendations/next');
    await choosePersona(view, 'Tomás Vega');
    expect(inFlight.cancelled).toBe(true);
    expect(view.el.querySelectorAll('app-where-next-cards .card').length).toBe(0);
  });

  it('switching persona after an answer clears her cards, notices and status line (PLAN §4.1)', async () => {
    const view = await start();
    await choosePersona(view, 'Aisha Rahman');
    await askWhereNext(view);
    view.http.expectOne('/api/v1/recommendations/next').flush(answer());
    await settle(view);
    // Aisha's answer is on screen, with her access-need notice, and nothing is in flight.
    view.http.verify();
    expect(view.el.querySelectorAll('app-where-next-cards .card').length).toBe(1);
    expect(text(view.el.querySelector('app-where-next-cards'))).toContain(STEP_FREE);
    expect(text(view.el.querySelector('[role="status"]'))).toContain(
      '1 suggestion for Aisha Rahman.',
    );

    await choosePersona(view, 'Tomás Vega');
    expect(view.el.querySelectorAll('app-where-next-cards .card').length).toBe(0);
    expect(text(view.el.querySelector('app-where-next-cards'))).toBe('');
    expect(text(view.el.querySelector('[role="status"]'))).toBe('');
    const button = view.el.querySelector<HTMLButtonElement>('#where-next-button')!;
    expect(text(button)).toBe('Where should I go next?');
    expect(button.disabled).toBe(false);
    view.http.expectNone('/api/v1/recommendations/next');
  });

  it('shows a friendly message when suggestions are down, and Try again asks once more', async () => {
    const view = await start();
    await choosePersona(view, 'Aisha Rahman');
    await askWhereNext(view);
    view.http
      .expectOne('/api/v1/recommendations/next')
      .flush('down', { status: 503, statusText: 'Service Unavailable' });
    await settle(view);
    const alert = view.el.querySelector('main [role="alert"]')!;
    expect(text(alert)).toContain("Suggestions aren't available right now");
    click(alert.querySelector('button'));
    await settle(view);
    expect(
      view.http.expectOne('/api/v1/recommendations/next').request.headers.get('X-Demo-Profile-ID'),
    ).toBe(AISHA.id);
  });
});
