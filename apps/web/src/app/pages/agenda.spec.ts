import { TestBed } from '@angular/core/testing';

import { PersonaService } from '../personas';
import { AgendaResponse } from '../portal';
import { AISHA, PERSONAS, TOMAS, View, click, render, settle, text, texts } from '../test-support';
import { AgendaPage } from './agenda';

const AGENDA_URL = '/api/v1/me/agenda';

const AGENDA: AgendaResponse = {
  profileId: AISHA.id,
  displayName: 'Aisha Rahman',
  bookmarked: [
    {
      sessionId: 'k-1',
      title: 'Observability for AI SRE Teams',
      event: 'KubeCon + CloudNativeCon North America 2026',
      conference: 'kubecon-na-2026',
      isFictional: false,
      startsAt: '2026-11-10T17:30:00Z',
      endsAt: '2026-11-10T18:00:00Z',
      startsAtVenue: 'Tue Nov 10, 10:30 AM MST',
      room: 'Salt Palace | Level 1 | Hall DE',
      level: null,
      topics: ['Observability'],
    },
  ],
  attended: [
    {
      sessionId: 'cb-2',
      title: 'AI SRE in Practice: What Our Agent Got Wrong',
      event: 'CloudBridge Summit 2026',
      conference: 'cloudbridge-summit-2026',
      isFictional: true,
      startsAt: '2026-10-06T16:00:00Z',
      endsAt: '2026-10-06T16:45:00Z',
      startsAtVenue: 'Tue Oct 6, 10:00 AM MDT',
      room: 'Cascade',
      level: 'advanced',
      topics: ['ai-sre', 'incident-response'],
    },
  ],
};

async function start(selected = ''): Promise<View<AgendaPage>> {
  const view = render(AgendaPage);
  const personas = TestBed.inject(PersonaService);
  personas.personas.set(PERSONAS);
  personas.status.set('ready');
  personas.select(selected);
  await settle(view);
  return view;
}

describe('My agenda', () => {
  it('asks for a persona first, without calling the server', async () => {
    const view = await start();
    expect(text(view.el.querySelector('h1'))).toBe('My agenda');
    expect(text(view.el.querySelector('main, .page'))).toContain(
      'Choose a persona to see an agenda',
    );
    view.http.expectNone(AGENDA_URL);
  });

  it('loads the agenda for the persona in the header (AUTHZ-1) and lists bookmarks and attended talks', async () => {
    const view = await start(AISHA.id);
    const req = view.http.expectOne(AGENDA_URL);
    expect(req.request.method).toBe('GET');
    expect(req.request.urlWithParams).toBe(AGENDA_URL);
    expect(req.request.headers.get('X-Demo-Profile-ID')).toBe(AISHA.id);
    req.flush(AGENDA);
    await settle(view);

    expect(texts(view.el, 'h2')).toEqual(['Bookmarked 1', 'Attended 1']);
    const bookmarked = view.el.querySelector('.bookmarked app-session-card')!;
    expect(text(bookmarked.querySelector('h3'))).toBe('Observability for AI SRE Teams');
    expect(text(bookmarked.querySelector('.badge'))).toBe('Real event, official schedule');
    expect(text(bookmarked.querySelector('time'))).toBe('Tue Nov 10, 10:30 AM MST');
    expect(text(bookmarked)).toContain('Salt Palace | Level 1 | Hall DE');
    const attended = view.el.querySelector('.attended app-session-card')!;
    expect(text(attended.querySelector('.badge'))).toBe('Fictional demo event');
    expect(text(attended.querySelector('time'))).toBe('Tue Oct 6, 10:00 AM MDT');
    expect(texts(attended, '.chip')).toEqual(['ai-sre', 'incident-response']);
    expect(text(view.el.querySelector('[role="status"]'))).toContain('1 bookmarked, 1 attended');
  });

  it('says when a persona has nothing yet, instead of showing an empty list', async () => {
    const view = await start(TOMAS.id);
    view.http.expectOne(AGENDA_URL).flush({
      ...AGENDA,
      profileId: TOMAS.id,
      displayName: 'Tomás Vega',
      bookmarked: [],
      attended: [],
    });
    await settle(view);
    expect(text(view.el)).toContain("Tomás Vega hasn't bookmarked a talk yet");
    expect(text(view.el)).toContain('No attended talks on record for Tomás Vega');
  });

  it('reloads for the new persona on a switch, and drops the last one’s talks first', async () => {
    const view = await start(AISHA.id);
    view.http.expectOne(AGENDA_URL).flush(AGENDA);
    await settle(view);
    expect(view.el.querySelectorAll('app-session-card').length).toBe(2);

    TestBed.inject(PersonaService).select(TOMAS.id);
    await settle(view);
    expect(view.el.querySelectorAll('app-session-card').length).toBe(0);
    const req = view.http.expectOne(AGENDA_URL);
    expect(req.request.headers.get('X-Demo-Profile-ID')).toBe(TOMAS.id);
  });

  it('shows a friendly message when the agenda is down (503), and Try again asks once more', async () => {
    const view = await start(AISHA.id);
    view.http
      .expectOne(AGENDA_URL)
      .flush('down', { status: 503, statusText: 'Service Unavailable' });
    await settle(view);
    const alert = view.el.querySelector('[role="alert"]')!;
    expect(text(alert)).toContain("The agenda isn't available right now");
    click(alert.querySelector('button'));
    await settle(view);
    view.http.expectOne(AGENDA_URL).flush(AGENDA);
    await settle(view);
    expect(view.el.querySelectorAll('app-session-card').length).toBe(2);
  });

  it('says when the persona is unknown to the server (404) rather than failing', async () => {
    const view = await start(AISHA.id);
    view.http
      .expectOne(AGENDA_URL)
      .flush({ detail: 'Profile not found' }, { status: 404, statusText: 'Not Found' });
    await settle(view);
    expect(text(view.el.querySelector('[role="alert"]'))).toContain(
      "This persona isn't on the server",
    );
  });
});
