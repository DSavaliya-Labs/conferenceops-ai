import { AccessFactList, EventList } from '../portal';
import { View, click, render, settle, text, texts } from '../test-support';
import { AccessibilityPage } from './accessibility';

const ACCESS_PAGE =
  'https://events.linuxfoundation.org/kubecon-cloudnativecon-north-america/attend/inclusion-accessibility/';

const EVENTS: EventList = {
  events: [
    {
      slug: 'kubecon-na-2026',
      name: 'KubeCon + CloudNativeCon North America 2026',
      startsOn: '2026-11-09',
      endsOn: '2026-11-12',
      timezone: 'America/Denver',
      isFictional: false,
      hasTranscripts: false,
      counts: { sessions: 600, speakers: 716, rooms: 37, accessFacts: 7 },
    },
    {
      slug: 'cloudbridge-summit-2026',
      name: 'CloudBridge Summit 2026',
      startsOn: '2026-10-06',
      endsOn: '2026-10-08',
      timezone: 'America/Denver',
      isFictional: true,
      hasTranscripts: true,
      counts: { sessions: 24, speakers: 12, rooms: 4, accessFacts: 0 },
    },
  ],
};

const fact = (id: string, kind: string, factText: string, location: string | null = null) => ({
  id,
  kind,
  text: factText,
  location,
  sourceUrl: ACCESS_PAGE,
  readOn: '2026-11-06',
});

const KUBECON_FACTS: AccessFactList = {
  conference: 'kubecon-na-2026',
  event: 'KubeCon + CloudNativeCon North America 2026',
  isFictional: false,
  facts: [
    fact(
      'f0',
      'captions',
      'Captioning in 60+ languages through Wordly, with instructions in each breakout room.',
    ),
    fact(
      'f1',
      'reserved_seating',
      'Reserved seating in each session for attendees with accessibility needs.',
    ),
    fact('f2', 'quiet_room', 'All-Attendee Quiet Room.', 'Salt Palace, Level 2, room 260A'),
    fact('f3', 'quiet_room', 'Women + Non-Binary Quiet Room.', 'Salt Palace, Level 2, room 260B'),
    fact('f4', 'prayer_room', 'Prayer Room.', 'Hyatt Regency, Level 5, Zion'),
    fact(
      'f5',
      'staff_help',
      'For help locating an elevator or other accessibility questions, ask Linux Foundation staff at the ' +
        'registration or information desks.',
    ),
    fact(
      'f6',
      'mobility_rental',
      'Wheelchair and scooter rental through Mobility City; contact them directly for rates and availability.',
    ),
  ],
};

const CLOUDBRIDGE_FACTS: AccessFactList = {
  conference: 'cloudbridge-summit-2026',
  event: 'CloudBridge Summit 2026',
  isFictional: true,
  facts: [],
};

async function loaded(): Promise<View<AccessibilityPage>> {
  const view = render(AccessibilityPage);
  view.http.expectOne('/api/v1/conferences').flush(EVENTS);
  await settle(view);
  view.http.expectOne('/api/v1/conferences/kubecon-na-2026/access-facts').flush(KUBECON_FACTS);
  view.http
    .expectOne('/api/v1/conferences/cloudbridge-summit-2026/access-facts')
    .flush(CLOUDBRIDGE_FACTS);
  await settle(view);
  return view;
}

describe('Accessibility at the venue', () => {
  it('shows each event’s stored facts, grouped, each with its source link and the date it was read (ACCESS-1)', async () => {
    const view = await loaded();
    expect(text(view.el.querySelector('h1'))).toBe('Accessibility at the venue');
    expect(texts(view.el, 'h2')).toEqual([
      'KubeCon + CloudNativeCon North America 2026',
      'CloudBridge Summit 2026',
    ]);
    expect(texts(view.el, 'h3')).toEqual([
      'Captioning',
      'Reserved seating',
      'Quiet rooms',
      'Prayer room',
      'Help from staff',
      'Wheelchair and scooter rental',
    ]);
    const facts = Array.from(view.el.querySelectorAll('.fact'));
    expect(facts.length).toBe(7);
    for (const item of facts) {
      expect(item.querySelector('a')?.getAttribute('href')).toBe(ACCESS_PAGE);
      expect(item.querySelector('a')?.getAttribute('rel')).toContain('noopener');
    }
    expect(texts(view.el, '.location')).toEqual([
      'Salt Palace, Level 2, room 260A',
      'Salt Palace, Level 2, room 260B',
      'Hyatt Regency, Level 5, Zion',
    ]);
    expect(text(view.el)).toContain('7 published facts, read on Nov 6, 2026');
    expect(text(view.el.querySelector('[role="status"]'))).toContain(
      '7 published access facts loaded',
    );
  });

  it('shows nothing invented for an event that publishes no facts', async () => {
    const view = await loaded();
    const sections = Array.from(view.el.querySelectorAll('section.event'));
    expect(text(sections[1])).toContain('publishes no access facts');
    expect(sections[1].querySelectorAll('.fact').length).toBe(0);
    expect(text(sections[1].querySelector('.badge'))).toBe('Fictional demo event');
  });

  it('never labels anything step-free: the only mention says routes are not published, and points to staff', async () => {
    const view = await loaded();
    const words = (view.el.textContent ?? '').toLowerCase();
    expect(words.split('step-free').length - 1).toBe(2); // both inside the one "Not published" statement
    const notice = text(view.el.querySelector('.callout-warn'));
    expect(notice).toContain('Not published: step-free routes, walk times and room capacities');
    expect(notice).toContain('ask Linux Foundation staff at the registration or information desks');
    expect(view.el.querySelectorAll('.callout-warn').length).toBe(1); // not for the event without facts
  });

  it('shows a friendly message when the facts are down, and Try again asks once more', async () => {
    const view = render(AccessibilityPage);
    view.http
      .expectOne('/api/v1/conferences')
      .flush('down', { status: 503, statusText: 'Service Unavailable' });
    await settle(view);
    const alert = view.el.querySelector('[role="alert"]')!;
    expect(text(alert)).toContain("The access facts aren't available right now");
    click(alert.querySelector('button'));
    await settle(view);
    view.http.expectOne('/api/v1/conferences').flush(EVENTS);
    await settle(view);
    view.http.expectOne('/api/v1/conferences/kubecon-na-2026/access-facts').flush(KUBECON_FACTS);
    view.http
      .expectOne('/api/v1/conferences/cloudbridge-summit-2026/access-facts')
      .flush(CLOUDBRIDGE_FACTS);
    await settle(view);
    expect(view.el.querySelectorAll('.fact').length).toBe(7);
  });
});
