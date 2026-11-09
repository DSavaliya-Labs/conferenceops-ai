import { TestBed } from '@angular/core/testing';

import { PersonaService } from '../personas';
import { AISHA, PERSONAS, TOMAS, View, click, render, settle, text, texts } from '../test-support';
import { CommunityFeed, CommunityPage, FEED_URL } from './community';

const FEED: CommunityFeed = {
  isCommunityContent: true,
  posts: [
    {
      id: 'p-2',
      author: 'Grace Mensah',
      authorRole: 'attendee',
      body: 'First KubeCon. Any tips?',
      createdAt: '2026-11-09T16:00:00Z',
      replies: [],
    },
    {
      id: 'p-1',
      author: 'Aisha Rahman',
      authorRole: 'attendee',
      body: 'Who is following the agent-reliability talks?',
      createdAt: '2026-11-09T15:00:00Z',
      replies: [
        {
          id: 'r-1',
          author: 'Lin Zhao',
          authorRole: 'attendee',
          body: 'Me, for the security side.',
          createdAt: '2026-11-09T15:10:00Z',
          parentId: 'p-1',
          replies: [],
        },
      ],
    },
  ],
};

async function start(selected = AISHA.id): Promise<View<CommunityPage>> {
  const view = render(CommunityPage);
  const personas = TestBed.inject(PersonaService);
  personas.personas.set(PERSONAS);
  personas.status.set('ready');
  personas.select(selected);
  await settle(view);
  return view;
}

function type(view: View<CommunityPage>, selector: string, value: string): void {
  const field = view.el.querySelector<HTMLTextAreaElement>(selector)!;
  field.value = value;
  field.dispatchEvent(new Event('input'));
  view.fixture.detectChanges();
}

function submit(view: View<CommunityPage>, selector: string): void {
  view.el.querySelector<HTMLFormElement>(selector)!.dispatchEvent(new Event('submit'));
  view.fixture.detectChanges();
}

describe('Community', () => {
  it('lists posts newest first with their replies, labeled as community content by fictional personas', async () => {
    const view = await start();
    view.http.expectOne(FEED_URL).flush(FEED);
    await settle(view);

    expect(text(view.el.querySelector('h1'))).toBe('Community');
    expect(text(view.el.querySelector('.lead'))).toContain('not official event information');
    expect(texts(view.el, '.feed > li > article > .body')).toEqual([
      'First KubeCon. Any tips?',
      'Who is following the agent-reliability talks?',
    ]);
    expect(texts(view.el, '.replies .body')).toEqual(['Me, for the security side.']);
    expect(text(view.el.querySelector('.post .meta'))).toContain('Fictional persona');
    // Venue time, in the event's zone (TIME-1).
    expect(text(view.el.querySelector('.post time'))).toContain('MST');
  });

  it('posts as the persona in the header (AUTHZ-1), announces it and reloads the feed', async () => {
    const view = await start();
    view.http.expectOne(FEED_URL).flush(FEED);
    await settle(view);

    const label = view.el.querySelector('label[for="community-body"]');
    expect(text(label)).toBe('Write a post as Aisha Rahman');
    const button = view.el.querySelector<HTMLButtonElement>('.composer button[type="submit"]')!;
    expect(button.disabled).toBe(true);
    type(view, '#community-body', '  Anyone for the eBPF talks?  ');
    expect(button.disabled).toBe(false);
    submit(view, '.composer');

    const req = view.http.expectOne((r) => r.method === 'POST' && r.url === FEED_URL);
    expect(req.request.headers.get('X-Demo-Profile-ID')).toBe(AISHA.id);
    expect(req.request.body).toEqual({ body: 'Anyone for the eBPF talks?' });
    req.flush({ ...FEED.posts[0], id: 'p-3', body: 'Anyone for the eBPF talks?' });
    await settle(view);
    expect(texts(view.el, '[role="status"]')).toEqual(['Your post is published.']);
    view.http.expectOne((r) => r.method === 'GET' && r.url === FEED_URL).flush(FEED);
    await settle(view);
    expect(view.el.querySelector<HTMLTextAreaElement>('#community-body')!.value).toBe('');
  });

  it('replies to a post through a labeled form that the Reply button discloses', async () => {
    const view = await start();
    view.http.expectOne(FEED_URL).flush(FEED);
    await settle(view);

    const replyButton = view.el.querySelectorAll<HTMLButtonElement>('.post .actions button')[1];
    expect(text(replyButton)).toContain('Reply to Aisha Rahman');
    expect(replyButton.getAttribute('aria-expanded')).toBe('false');
    click(replyButton);
    await settle(view);
    expect(replyButton.getAttribute('aria-expanded')).toBe('true');
    expect(text(view.el.querySelector('label[for="reply-body-p-1"]'))).toBe(
      'Reply to Aisha Rahman',
    );
    type(view, '#reply-body-p-1', 'Count me in');
    submit(view, '#reply-form-p-1');

    const req = view.http.expectOne((r) => r.method === 'POST' && r.url === FEED_URL);
    expect(req.request.body).toEqual({ body: 'Count me in', parentId: 'p-1' });
    req.flush({ ...FEED.posts[1].replies[0], id: 'r-2', body: 'Count me in' });
    await settle(view);
    expect(texts(view.el, '[role="status"]')).toEqual(['Your reply to Aisha Rahman is published.']);
    expect(view.el.querySelector('#reply-form-p-1')).toBeNull();
    view.http.expectOne((r) => r.method === 'GET' && r.url === FEED_URL).flush(FEED);
  });

  it('keeps the text and says so when a post fails', async () => {
    const view = await start(TOMAS.id);
    view.http.expectOne(FEED_URL).flush(FEED);
    await settle(view);
    type(view, '#community-body', 'GitOps stories?');
    submit(view, '.composer');
    view.http
      .expectOne((r) => r.method === 'POST')
      .flush({ detail: 'down' }, { status: 503, statusText: 'Unavailable' });
    await settle(view);
    expect(texts(view.el, '[role="status"]')).toEqual([
      "Your post wasn't published. Please try again.",
    ]);
    expect(view.el.querySelector<HTMLTextAreaElement>('#community-body')!.value).toBe(
      'GitOps stories?',
    );
  });

  it('shows the feed without a composer when no persona is chosen, and an error with a retry', async () => {
    const view = await start('');
    view.http.expectOne(FEED_URL).flush({}, { status: 503, statusText: 'Unavailable' });
    await settle(view);
    expect(view.el.querySelector('#community-body')).toBeNull();
    expect(text(view.el.querySelector('[role="alert"]'))).toContain(
      "The community feed isn't available right now",
    );
    click(view.el.querySelector('[role="alert"] button'));
    view.http.expectOne(FEED_URL).flush(FEED);
    await settle(view);
    expect(texts(view.el, '.feed > li')).toHaveLength(2);
  });
});
