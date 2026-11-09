import { EventList, formatDay, formatDayRange } from '../portal';
import { click, render, settle, text, texts } from '../test-support';
import { EventsPage } from './events';

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

describe('Events', () => {
  it('lists both events with their label, dates, zone and counts', async () => {
    const view = render(EventsPage);
    view.http.expectOne('/api/v1/conferences').flush(EVENTS);
    await settle(view);
    expect(text(view.el.querySelector('h1'))).toBe('Events');
    expect(texts(view.el, 'article h2')).toEqual([
      'KubeCon + CloudNativeCon North America 2026',
      'CloudBridge Summit 2026',
    ]);
    expect(texts(view.el, 'article .badge')).toEqual([
      'Real event, official schedule',
      'Fictional demo event',
    ]);
    const [kubecon, cloudbridge] = Array.from(view.el.querySelectorAll('article'));
    expect(text(kubecon.querySelector('.dates'))).toContain('Nov 9–12, 2026');
    expect(text(kubecon.querySelector('.dates'))).toContain('America/Denver');
    expect(texts(kubecon, 'dd')).toEqual(['600', '716', '37', '7']);
    expect(text(kubecon)).toContain('public Sessionize feed');
    expect(text(cloudbridge.querySelector('.dates'))).toContain('Oct 6–8, 2026');
    expect(text(cloudbridge)).toContain('invented');
    expect(texts(cloudbridge, 'dd')).toEqual(['24', '12', '4', '0']);
  });

  it('shows a friendly message when the events are down, and Try again asks once more', async () => {
    const view = render(EventsPage);
    view.http
      .expectOne('/api/v1/conferences')
      .flush('down', { status: 503, statusText: 'Service Unavailable' });
    await settle(view);
    const alert = view.el.querySelector('[role="alert"]')!;
    expect(text(alert)).toContain("The events aren't available right now");
    click(alert.querySelector('button'));
    await settle(view);
    view.http.expectOne('/api/v1/conferences').flush(EVENTS);
    await settle(view);
    expect(view.el.querySelectorAll('article').length).toBe(2);
  });
});

describe('Calendar days', () => {
  it('read ISO dates as days, never shifted by a time zone', () => {
    expect(formatDay('2026-11-06')).toBe('Nov 6, 2026');
    expect(formatDayRange('2026-11-09', '2026-11-12')).toBe('Nov 9–12, 2026');
    expect(formatDayRange('2026-10-30', '2026-11-02')).toBe('Oct 30 – Nov 2, 2026');
    expect(formatDayRange('2026-12-31', '2027-01-02')).toBe('Dec 31 – Jan 2, 2027');
    expect(formatDayRange('2026-11-09', '2026-11-09')).toBe('Nov 9, 2026');
  });
});
