import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { AgendaButton } from './agenda-button';
import { AgendaService } from './agenda-state';
import { PersonaService } from './personas';
import { AISHA, TOMAS, text } from './test-support';

const AGENDA_URL = '/api/v1/me/agenda';
const TALK = { id: 'talk-1', title: 'Tracing LLM Calls in Production' };

const OTHER = { id: 'talk-2', title: 'Operating Kubernetes Agents Safely' };

@Component({
  imports: [AgendaButton],
  template: `
    <app-agenda-button [sessionId]="talk().id" [title]="talk().title" />
    <app-agenda-button [sessionId]="other().id" [title]="other().title" />
  `,
})
class Host {
  readonly talk = signal(TALK);
  readonly other = signal(OTHER);
}

function setUp(bookmarked: string[] = []) {
  TestBed.configureTestingModule({
    imports: [Host],
    providers: [provideHttpClient(), provideHttpClientTesting()],
  });
  const personas = TestBed.inject(PersonaService);
  personas.personas.set([AISHA]);
  personas.select(AISHA.id);
  const fixture = TestBed.createComponent(Host);
  fixture.detectChanges();
  const http = TestBed.inject(HttpTestingController);
  // The service loads the persona's agenda once to know which talks are already on it.
  http.expectOne(AGENDA_URL).flush({
    profileId: AISHA.id,
    displayName: AISHA.displayName,
    bookmarked: bookmarked.map((sessionId) => ({ sessionId })),
    attended: [],
  });
  fixture.detectChanges();
  const buttons = () =>
    fixture.nativeElement.querySelectorAll('button') as NodeListOf<HTMLButtonElement>;
  const button = () => buttons()[0];
  const status = () => fixture.nativeElement.querySelector('[role="status"]') as HTMLElement;
  return { fixture, http, button, buttons, status, agenda: TestBed.inject(AgendaService) };
}

describe('Quick add to My Agenda', () => {
  afterEach(() => TestBed.inject(HttpTestingController).verify());

  it('offers to add a talk that is not on the agenda, naming the talk for screen readers', () => {
    const { button } = setUp();
    expect(button().getAttribute('aria-pressed')).toBe('false');
    expect(text(button())).toContain('Add to My Agenda');
    expect(text(button())).toContain(TALK.title);
  });

  it('adds with a PUT for the persona in the header, flips at once and announces the change', () => {
    const { fixture, http, button, status } = setUp();
    button().click();
    fixture.detectChanges();
    const req = http.expectOne(`${AGENDA_URL}/${TALK.id}`);
    expect(req.request.method).toBe('PUT');
    expect(req.request.headers.get('X-Demo-Profile-ID')).toBe(AISHA.id);
    expect(button().getAttribute('aria-pressed')).toBe('true'); // optimistic
    expect(button().disabled).toBe(true); // while the write is in flight
    req.flush(null, { status: 204, statusText: 'No Content' });
    fixture.detectChanges();
    expect(button().disabled).toBe(false);
    expect(text(button())).toContain('In My Agenda');
    // The service re-reads the agenda so the list pages agree with the card.
    const reread = http.expectOne(AGENDA_URL);
    expect(reread.request.method).toBe('GET');
    reread.flush({
      profileId: AISHA.id,
      displayName: AISHA.displayName,
      bookmarked: [{ sessionId: TALK.id }],
      attended: [],
    });
    fixture.detectChanges();
    expect(text(button())).toContain('In My Agenda');
    expect(text(status())).toContain(`Added “${TALK.title}” to My Agenda.`);
  });

  it('removes with a DELETE when the talk is already on the agenda', () => {
    const { fixture, http, button, status } = setUp([TALK.id]);
    expect(text(button())).toContain('In My Agenda');
    button().click();
    fixture.detectChanges();
    const req = http.expectOne(`${AGENDA_URL}/${TALK.id}`);
    expect(req.request.method).toBe('DELETE');
    req.flush(null, { status: 204, statusText: 'No Content' });
    fixture.detectChanges();
    expect(button().getAttribute('aria-pressed')).toBe('false');
    http
      .expectOne(AGENDA_URL)
      .flush({ profileId: AISHA.id, displayName: AISHA.displayName, bookmarked: [], attended: [] });
    fixture.detectChanges();
    expect(button().getAttribute('aria-pressed')).toBe('false');
    expect(text(status())).toContain(`Removed “${TALK.title}” from My Agenda.`);
  });

  it('rolls the button back and says so when the server refuses', () => {
    const { fixture, http, button, status } = setUp();
    button().click();
    fixture.detectChanges();
    http
      .expectOne(`${AGENDA_URL}/${TALK.id}`)
      .flush({ detail: 'Session not found' }, { status: 404, statusText: 'Not Found' });
    fixture.detectChanges();
    expect(button().getAttribute('aria-pressed')).toBe('false');
    expect(text(status())).toContain(`Couldn't add “${TALK.title}” to My Agenda right now.`);
  });

  it('is disabled when no persona is selected, and the agenda set is empty then', () => {
    const { fixture, button, agenda } = setUp();
    TestBed.inject(PersonaService).selectedId.set('');
    fixture.detectChanges();
    expect(button().disabled).toBe(true);
    expect(agenda.bookmarked().size).toBe(0);
  });

  it('lets a second card be added while the first write is still in flight (each card waits for its own)', () => {
    const { fixture, http, buttons } = setUp();
    buttons()[0].click();
    fixture.detectChanges();
    expect(buttons()[0].disabled).toBe(true);
    expect(buttons()[1].disabled).toBe(false);
    buttons()[1].click();
    fixture.detectChanges();
    const writes = http.match((req) => req.method === 'PUT');
    expect(writes.map((w) => w.request.url)).toEqual([
      `${AGENDA_URL}/${TALK.id}`,
      `${AGENDA_URL}/${OTHER.id}`,
    ]);
    writes.forEach((w) => w.flush(null, { status: 204, statusText: 'No Content' }));
    fixture.detectChanges();
    // Each success re-reads the agenda and the newest read wins: the earlier one is cancelled.
    http
      .match(AGENDA_URL)
      .filter((r) => !r.cancelled)
      .forEach((r) =>
        r.flush({
          profileId: AISHA.id,
          displayName: AISHA.displayName,
          bookmarked: [{ sessionId: TALK.id }, { sessionId: OTHER.id }],
          attended: [],
        }),
      );
    fixture.detectChanges();
    expect(Array.from(buttons()).map((b) => b.getAttribute('aria-pressed'))).toEqual([
      'true',
      'true',
    ]);
  });

  it('ignores a write that finishes after a persona switch: no reload of the old agenda, no announcement', () => {
    const { fixture, http, button, status } = setUp();
    button().click();
    fixture.detectChanges();
    const write = http.expectOne(`${AGENDA_URL}/${TALK.id}`);
    // Switch persona while the write is in flight: her agenda starts loading at once.
    const personas = TestBed.inject(PersonaService);
    personas.personas.set([AISHA, TOMAS]);
    personas.select(TOMAS.id);
    fixture.detectChanges();
    const tomasLoad = http.expectOne(AGENDA_URL);
    expect(tomasLoad.request.headers.get('X-Demo-Profile-ID')).toBe(TOMAS.id);
    write.flush(null, { status: 204, statusText: 'No Content' });
    fixture.detectChanges();
    http.expectNone(AGENDA_URL); // the old persona's write doesn't reload her agenda over Tomás's
    expect(text(status())).toBe('');
    tomasLoad.flush({
      profileId: TOMAS.id,
      displayName: TOMAS.displayName,
      bookmarked: [],
      attended: [],
    });
    fixture.detectChanges();
    expect(button().getAttribute('aria-pressed')).toBe('false'); // Tomás's agenda, not Aisha's write
  });
});
