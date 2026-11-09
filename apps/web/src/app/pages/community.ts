import { HttpClient } from '@angular/common/http';
import { Component, computed, effect, inject, signal, untracked } from '@angular/core';

import { Icon } from '../icons';
import { DEMO_PROFILE_HEADER, PersonaService } from '../personas';

/* The community feed's contract on core-api (contracts/openapi.json, API-1); restated here because the web image
 * builds from apps/web alone. */
export interface CommunityPost {
  id: string;
  author: string;
  authorRole: string;
  body: string;
  createdAt: string;
  parentId?: string | null;
  replies: CommunityPost[];
}

export interface CommunityFeed {
  posts: CommunityPost[];
  isCommunityContent: boolean;
}

export const FEED_URL = '/api/v1/community/posts';
export const MAX_BODY = 500;

type FeedStatus = 'loading' | 'ready' | 'error';

/** Times in the venue's time zone, the same wording for every attendee (TIME-1). */
const VENUE_TIME = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/Denver',
  weekday: 'short',
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
  timeZoneName: 'short',
});

/** Community: posts and one level of replies from the demo personas. Community content, labeled as such, never an
 *  official fact (ACCESS-1). The author is always the persona in X-Demo-Profile-ID (AUTHZ-1); a post's text stays out
 *  of telemetry (PRIV-1). Keyboard-first: labeled fields, one polite status line, focus kept on the form (A11Y-1). */
@Component({
  selector: 'app-community',
  imports: [Icon],
  template: `
    <div class="page">
      <header class="page-header">
        <h1>Community</h1>
        <p class="lead">
          Posts and replies from the attendees on this platform. Every author is a fictional demo
          persona. This is community content, not official event information: access facts come only
          from the Accessibility page, with their sources.
        </p>
      </header>

      <p class="visually-hidden" role="status" aria-live="polite">{{ announcement() }}</p>

      @if (personas.selected(); as me) {
        <form class="card composer" (submit)="post($event)">
          <label for="community-body">Write a post as {{ me.displayName }}</label>
          <textarea
            id="community-body"
            class="control"
            rows="3"
            [attr.maxlength]="max"
            aria-describedby="community-count"
            [value]="draft()"
            (input)="draft.set(value($event))"
          ></textarea>
          <p class="actions">
            <span id="community-count" class="muted">{{ draft().length }} / {{ max }}</span>
            <button type="submit" class="btn btn-primary" [disabled]="!canPost()">
              <app-icon name="arrow-right" [size]="16" />
              Post
            </button>
          </p>
        </form>
      } @else {
        <p class="callout">Choose a persona in the account menu at the top right to post.</p>
      }

      @switch (status()) {
        @case ('loading') {
          <p class="muted">Loading the community feed…</p>
        }
        @case ('error') {
          <div class="callout callout-danger" role="alert">
            <p><strong>The community feed isn't available right now.</strong></p>
            <button type="button" class="btn btn-outline" (click)="load()">
              <app-icon name="refresh" [size]="18" />
              Try again
            </button>
          </div>
        }
        @case ('ready') {
          @if (posts().length === 0) {
            <p class="muted">No posts yet. Be the first.</p>
          }
          <ol class="feed">
            @for (p of posts(); track p.id) {
              <li>
                <article class="card post" [attr.aria-labelledby]="'post-' + p.id">
                  <p class="meta">
                    <strong [id]="'post-' + p.id">{{ p.author }}</strong>
                    <span class="badge">{{ p.authorRole }}</span>
                    <span class="badge">Fictional persona</span>
                    <time class="muted" [attr.datetime]="p.createdAt">{{ when(p.createdAt) }}</time>
                  </p>
                  <p class="body">{{ p.body }}</p>
                  @if (p.replies.length > 0) {
                    <ol class="replies" [attr.aria-label]="'Replies to ' + p.author">
                      @for (r of p.replies; track r.id) {
                        <li>
                          <p class="meta">
                            <strong>{{ r.author }}</strong>
                            <span class="badge">{{ r.authorRole }}</span>
                            <time class="muted" [attr.datetime]="r.createdAt">{{
                              when(r.createdAt)
                            }}</time>
                          </p>
                          <p class="body">{{ r.body }}</p>
                        </li>
                      }
                    </ol>
                  }
                  @if (personas.selected()) {
                    <p class="actions">
                      <button
                        type="button"
                        class="btn btn-outline"
                        [attr.aria-expanded]="replyTo() === p.id"
                        [attr.aria-controls]="'reply-form-' + p.id"
                        (click)="toggleReply(p.id)"
                      >
                        Reply<span class="visually-hidden"> to {{ p.author }}</span>
                      </button>
                    </p>
                    @if (replyTo() === p.id) {
                      <form
                        class="reply-form"
                        [id]="'reply-form-' + p.id"
                        (submit)="reply($event, p)"
                      >
                        <label [for]="'reply-body-' + p.id">Reply to {{ p.author }}</label>
                        <textarea
                          [id]="'reply-body-' + p.id"
                          class="control"
                          rows="2"
                          [attr.maxlength]="max"
                          [value]="replyDraft()"
                          (input)="replyDraft.set(value($event))"
                        ></textarea>
                        <p class="actions">
                          <button type="submit" class="btn btn-primary" [disabled]="!canReply()">
                            Post reply
                          </button>
                        </p>
                      </form>
                    }
                  }
                </article>
              </li>
            }
          </ol>
        }
      }
    </div>
  `,
  styles: `
    .composer,
    .reply-form {
      display: grid;
      gap: var(--space-2);
    }
    .composer {
      margin-bottom: var(--space-5);
    }
    .composer label,
    .reply-form label {
      font-weight: 600;
    }
    textarea.control {
      min-height: 5rem;
      padding: var(--space-2) var(--space-3);
      font: inherit;
      resize: vertical;
    }
    .feed,
    .replies {
      list-style: none;
      margin: 0;
      padding: 0;
      display: grid;
      gap: var(--space-3);
    }
    .replies {
      margin-top: var(--space-3);
      padding-left: var(--space-4);
      border-left: 2px solid var(--line-strong);
    }
    .meta {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: var(--space-2);
      margin: 0;
    }
    .body {
      margin: var(--space-2) 0 0;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .reply-form {
      margin-top: var(--space-3);
    }
  `,
})
export class CommunityPage {
  private readonly http = inject(HttpClient);
  protected readonly personas = inject(PersonaService);

  protected readonly max = MAX_BODY;
  protected readonly status = signal<FeedStatus>('loading');
  protected readonly posts = signal<CommunityPost[]>([]);
  protected readonly draft = signal('');
  protected readonly replyTo = signal<string | null>(null);
  protected readonly replyDraft = signal('');
  protected readonly busy = signal(false);
  protected readonly announcement = signal('');

  protected readonly canPost = computed(() => !this.busy() && this.draft().trim().length > 0);
  protected readonly canReply = computed(() => !this.busy() && this.replyDraft().trim().length > 0);

  constructor() {
    this.load();
    // A persona switch closes an open reply, so nothing typed as one persona is posted as another.
    effect(() => {
      this.personas.selectedId();
      untracked(() => {
        this.replyTo.set(null);
        this.replyDraft.set('');
      });
    });
  }

  load(): void {
    this.status.set('loading');
    this.http.get<CommunityFeed>(FEED_URL).subscribe({
      next: (feed) => {
        this.posts.set(feed.posts);
        this.status.set('ready');
      },
      error: () => this.status.set('error'),
    });
  }

  protected when(iso: string): string {
    return VENUE_TIME.format(new Date(iso));
  }

  protected value(event: Event): string {
    return (event.target as HTMLTextAreaElement).value;
  }

  protected toggleReply(id: string): void {
    this.replyTo.set(this.replyTo() === id ? null : id);
    this.replyDraft.set('');
  }

  protected post(event: Event): void {
    event.preventDefault();
    if (!this.canPost()) {
      return;
    }
    this.send({ body: this.draft().trim() }, () => {
      this.draft.set('');
      this.announcement.set('Your post is published.');
    });
  }

  protected reply(event: Event, parent: CommunityPost): void {
    event.preventDefault();
    if (!this.canReply()) {
      return;
    }
    this.send({ body: this.replyDraft().trim(), parentId: parent.id }, () => {
      this.replyTo.set(null);
      this.replyDraft.set('');
      this.announcement.set(`Your reply to ${parent.author} is published.`);
    });
  }

  private send(body: { body: string; parentId?: string }, done: () => void): void {
    const author = this.personas.selectedId();
    if (!author) {
      return;
    }
    this.busy.set(true);
    this.http
      .post<CommunityPost>(FEED_URL, body, { headers: { [DEMO_PROFILE_HEADER]: author } })
      .subscribe({
        next: () => {
          this.busy.set(false);
          done();
          this.load();
        },
        error: () => {
          this.busy.set(false);
          // The text stays in the field, so nothing typed is lost.
          this.announcement.set("Your post wasn't published. Please try again.");
        },
      });
  }
}
