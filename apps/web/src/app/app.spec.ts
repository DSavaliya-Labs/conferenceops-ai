import { NAV_LINKS } from './app';
import { scrubUrlAttributes } from './telemetry';
import {
  AISHA,
  View,
  choosePersona,
  click,
  key,
  loadPersonas,
  navigate,
  renderApp,
  settle,
  text,
  texts,
} from './test-support';

const HIT = {
  id: 's1',
  title: 'Policy as Code',
  event: 'KubeCon + CloudNativeCon North America 2026',
  conference: 'kubecon-na-2026',
  isFictional: false,
  startsAt: '2026-11-10T17:30:00Z',
  startsAtVenue: 'Tue Nov 10, 10:30 AM MST',
  room: 'Salt Palace | Level 1 | 155',
  level: 'intermediate',
  topics: ['Security'],
};

async function start(): Promise<View<import('./app').App>> {
  const view = renderApp();
  await loadPersonas(view);
  await navigate(view, '/talks');
  return view;
}

async function ask(view: View<unknown>, words: string): Promise<void> {
  const input = view.el.querySelector<HTMLInputElement>('#ask-input')!;
  input.value = words;
  input.dispatchEvent(new Event('input'));
  view.el
    .querySelector('form[role="search"]')!
    .dispatchEvent(new Event('submit', { cancelable: true }));
  await settle(view);
}

describe('App shell', () => {
  it('has a skip link, landmarks, one h1 per page and the Ask bar with its label (A11Y-1)', async () => {
    const view = await start();
    expect(view.el.querySelector('a.skip-link')?.getAttribute('href')).toBe('#main');
    expect(view.el.querySelector('header')).not.toBeNull();
    expect(view.el.querySelector('nav[aria-label="Main"]')).not.toBeNull();
    expect(view.el.querySelector('main#main')?.getAttribute('tabindex')).toBe('-1');
    expect(view.el.querySelector('footer')).not.toBeNull();
    expect(view.el.querySelectorAll('h1').length).toBe(1);
    expect(text(view.el.querySelector('h1'))).toBe('Find talks');
    expect(view.el.querySelector('form[role="search"]')).not.toBeNull();
    expect(text(view.el.querySelector('label[for="ask-input"]'))).toContain('Ask about talks');
  });

  it('lists every feature in the navigation and marks the current page', async () => {
    const view = await start();
    expect(texts(view.el, 'nav[aria-label="Main"] a span')).toEqual(
      NAV_LINKS.map((link) => link.label),
    );
    expect(texts(view.el, 'nav[aria-label="Main"] a span')).toEqual([
      'Find talks',
      'Where next',
      'My agenda',
      'Events',
      'Accessibility at the venue',
      'How this platform runs',
    ]);
    const current = () => texts(view.el, 'nav[aria-label="Main"] a[aria-current="page"] span');
    expect(current()).toEqual(['Find talks']);

    await navigate(view, '/events');
    expect(current()).toEqual(['Events']);
    expect(text(view.el.querySelector('h1'))).toBe('Events');
    expect(view.el.querySelectorAll('h1').length).toBe(1);
    view.http.expectOne('/api/v1/conferences');
  });

  it('opens the narrow-screen navigation from the menu button, and Escape closes it and returns focus', async () => {
    const view = await start();
    const toggle = view.el.querySelector<HTMLButtonElement>('.nav-toggle')!;
    const nav = view.el.querySelector('#site-nav')!;
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(toggle.getAttribute('aria-controls')).toBe('site-nav');
    expect(nav.classList.contains('open')).toBe(false);

    toggle.click();
    await settle(view);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(nav.classList.contains('open')).toBe(true);

    nav.querySelector<HTMLAnchorElement>('a')!.focus();
    key(nav, 'Escape');
    await settle(view);
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(nav.classList.contains('open')).toBe(false);
    expect(document.activeElement).toBe(toggle);
  });

  it('closes the narrow-screen navigation after following a link, and moves focus to the page', async () => {
    const view = await start();
    view.el.querySelector<HTMLButtonElement>('.nav-toggle')!.click();
    await settle(view);
    const link = Array.from(view.el.querySelectorAll<HTMLAnchorElement>('#site-nav a')).find((a) =>
      a.textContent?.includes('How this platform runs'),
    )!;
    link.click();
    await settle(view);
    expect(view.router.url).toBe('/operations');
    expect(view.el.querySelector('#site-nav')?.classList.contains('open')).toBe(false);
    expect(document.activeElement).toBe(view.el.querySelector('#main'));
    view.http.expectOne('/config.json');
  });
});

describe('The Ask bar', () => {
  it('asks for words instead of searching for nothing', async () => {
    const view = await start();
    await ask(view, '   ');
    view.http.expectNone('/api/v1/search');
    expect(text(view.el.querySelector('#ask-error'))).toContain('Type what you are looking for');
    expect(view.el.querySelector('#ask-input')?.getAttribute('aria-invalid')).toBe('true');
  });

  it('sends the query in the request body, never the URL (PRIV-1), opens Find talks and lists the results', async () => {
    const view = await start();
    await navigate(view, '/events');
    view.http.expectOne('/api/v1/conferences');
    await ask(view, 'kubernetes security');
    const req = view.http.expectOne('/api/v1/search');
    expect(req.request.method).toBe('POST');
    expect(req.request.urlWithParams).toBe('/api/v1/search');
    expect(req.request.body).toEqual({ query: 'kubernetes security' });
    expect(view.router.url).toBe('/talks');
    req.flush({ results: [HIT], retrievalMode: 'hybrid' });
    await settle(view);

    const card = view.el.querySelector('.results app-session-card')!;
    expect(text(card.querySelector('h3'))).toBe('Policy as Code');
    expect(text(card.querySelector('.badge'))).toBe('Real event, official schedule');
    expect(card.querySelector('time')?.getAttribute('datetime')).toBe('2026-11-10T17:30:00Z');
    expect(text(card.querySelector('time'))).toBe('Tue Nov 10, 10:30 AM MST');
    expect(text(card)).toContain('Salt Palace | Level 1 | 155');
    expect(text(card)).toContain('intermediate');
    expect(texts(card, '.chip')).toEqual(['Security']);
    expect(text(view.el.querySelector('[role="status"]'))).toContain('1 talk found');
    expect(view.el.querySelector('.results-section .callout')).toBeNull();
  });

  it('labels a fictional result, and says when the match was by keywords only', async () => {
    const view = await start();
    await ask(view, 'ebpf');
    view.http.expectOne('/api/v1/search').flush({
      results: [
        {
          ...HIT,
          id: 'c1',
          event: 'CloudBridge Summit 2026',
          isFictional: true,
          level: null,
          topics: [],
        },
      ],
      retrievalMode: 'lexical',
    });
    await settle(view);
    expect(text(view.el.querySelector('.results .badge'))).toBe('Fictional demo event');
    expect(text(view.el.querySelector('.results-section .callout'))).toContain('keywords only');
  });

  it('says so when nothing matches', async () => {
    const view = await start();
    await ask(view, 'zebrafish');
    view.http.expectOne('/api/v1/search').flush({ results: [], retrievalMode: 'hybrid' });
    await settle(view);
    expect(text(view.el.querySelector('main h2'))).toContain('No talks match “zebrafish”');
    expect(text(view.el.querySelector('[role="status"]'))).toContain('No talks found');
  });

  it('shows a friendly message when search is down (503), and Try again sends the same search', async () => {
    const view = await start();
    await ask(view, 'observability');
    view.http
      .expectOne('/api/v1/search')
      .flush('down', { status: 503, statusText: 'Service Unavailable' });
    await settle(view);
    const alert = view.el.querySelector('main [role="alert"]')!;
    expect(text(alert)).toContain("Search isn't available right now");

    click(alert.querySelector('button'));
    await settle(view);
    const retry = view.http.expectOne('/api/v1/search');
    expect(retry.request.body).toEqual({ query: 'observability' });
    retry.flush({ results: [HIT], retrievalMode: 'hybrid' });
    await settle(view);
    expect(view.el.querySelector('main [role="alert"]')).toBeNull();
    expect(view.el.querySelectorAll('.results app-session-card').length).toBe(1);
  });

  it('also asks the archive, and shows the cited answer under the results with its replay link', async () => {
    const view = await start();
    await ask(view, 'what did the agent get wrong');
    view.http.expectOne('/api/v1/search').flush({ results: [HIT], retrievalMode: 'hybrid' });
    const asked = view.http.expectOne('/api/v1/ask');
    expect(asked.request.method).toBe('POST');
    expect(asked.request.body).toEqual({ query: 'what did the agent get wrong' });
    asked.flush({
      answer: 'The agent blamed a healthy database; a config push had emptied the connection pool.',
      answerSource: 'model',
      citations: [
        {
          chunkId: 't:1:0',
          sessionId: 'cb-2',
          sessionTitle: 'AI SRE in Practice: What Our Agent Got Wrong',
          speaker: 'Daniel Kowalczyk',
          startSeconds: 18.98,
          endSeconds: 27.3,
          replayPath: '/sessions/cb-2/replay?t=18.98',
          excerpt: 'The database was fine.',
          label: 'Simulated excerpt',
        },
      ],
      retrievalMode: 'hybrid',
      event: 'CloudBridge Summit 2026',
      isFictional: true,
    });
    await settle(view);
    const archive = view.el.querySelector('main .ask-answer')!;
    expect(text(archive.querySelector('h2'))).toBe('From the CloudBridge archive');
    expect(text(archive)).toContain('Simulated excerpt');
    expect(archive.querySelector('.citations a')?.getAttribute('href')).toBe(
      '/sessions/cb-2/replay?t=18.98',
    );
    expect(view.el.querySelectorAll('.results app-session-card').length).toBe(1);
  });

  it('renders a replay route inside the shell, with the navigation still there', async () => {
    const view = await start();
    await navigate(view, '/sessions/cb-2/replay?t=18.98');
    view.http
      .expectOne('/api/v1/sessions/cb-2/replay')
      .flush({ detail: 'No replay' }, { status: 404, statusText: 'Not Found' });
    await settle(view);
    expect(view.el.querySelector('nav[aria-label="Main"]')).not.toBeNull();
    expect(text(view.el.querySelector('main [role="alert"]'))).toContain(
      'This session has no replay',
    );
    expect(view.el.querySelector('nav[aria-label="Main"] a[aria-current="page"]')).toBeNull();
  });

  it('keeps the results when the attendee moves to another page and back', async () => {
    const view = await start();
    await ask(view, 'policy');
    view.http.expectOne('/api/v1/search').flush({ results: [HIT], retrievalMode: 'hybrid' });
    await settle(view);
    await navigate(view, '/where-next');
    expect(view.el.querySelector('.results')).toBeNull();
    await navigate(view, '/talks');
    expect(view.el.querySelectorAll('.results app-session-card').length).toBe(1);
    expect(view.el.querySelector<HTMLInputElement>('#ask-input')?.value).toBe('policy');
  });

  it('“Where next?” opens the page and asks for the chosen persona, with her ID in the header (AUTHZ-1)', async () => {
    const view = await start();
    await choosePersona(view, 'Aisha Rahman');
    click(view.el.querySelector('form[role="search"] .btn-accent'));
    await settle(view);
    expect(view.router.url).toBe('/where-next');
    const req = view.http.expectOne('/api/v1/recommendations/next');
    expect(req.request.headers.get('X-Demo-Profile-ID')).toBe(AISHA.id);
    expect(req.request.body).toEqual({ window: 'today', limit: 3 });
  });

  it('“Where next?” without a persona opens the page and asks for one instead of sending a request', async () => {
    const view = await start();
    click(view.el.querySelector('form[role="search"] .btn-accent'));
    await settle(view);
    expect(view.router.url).toBe('/where-next');
    view.http.expectNone('/api/v1/recommendations/next');
    expect(text(view.el.querySelector('form.where-next [role="alert"]'))).toContain(
      'Choose a demo persona first',
    );
  });

  it('has the three inline buttons in one field: the microphone right after the input, Search, Where next?', async () => {
    const view = await start();
    const field = view.el.querySelector('form[role="search"] .field')!;
    const input = field.querySelector('#ask-input')!;
    expect(input.nextElementSibling?.tagName.toLowerCase()).toBe('app-dictation-button');
    expect(texts(field, 'button')).toEqual(['Dictate', 'Search', 'Where next?']);
    expect(view.el.querySelector('#dictation-status')?.getAttribute('aria-live')).toBe('polite');
  });
});

describe('scrubUrlAttributes (PRIV-1)', () => {
  it('removes query strings and fragments from URL attributes', () => {
    const out = scrubUrlAttributes({
      'url.full': 'https://x.example/search?q=quiet+room#top',
      'url.query': 'q=quiet+room',
      'page.url.fragment': 'top',
      'url.path': '/search',
    });
    expect(out['url.full']).toBe('https://x.example/search');
    expect(out['url.query']).toBe('REDACTED');
    expect(out['page.url.fragment']).toBe('REDACTED');
    expect(out['url.path']).toBe('/search');
  });
});
