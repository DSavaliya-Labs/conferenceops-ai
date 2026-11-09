import { HttpErrorResponse } from '@angular/common/http';
import { Service, computed, effect, inject, signal, untracked } from '@angular/core';
import { Subscription } from 'rxjs';

import { PersonaService } from './personas';
import { AgendaResponse, PortalService } from './portal';

export type AgendaStatus = 'none' | 'loading' | 'done' | 'error' | 'missing';

/** The current persona's agenda, loaded once per persona and shared by the agenda page and every talk card's
 *  quick add. The server owns it (core-api, behind X-Demo-Profile-ID, AUTHZ-1): a write is optimistic on the card,
 *  then the agenda is re-read, and a refused write rolls the card back. A bookmark is a personal plan: it never
 *  reserves a seat. Switching persona drops the last one's agenda first (PLAN §4.1). */
@Service()
export class AgendaService {
  private readonly portal = inject(PortalService);
  private readonly personas = inject(PersonaService);
  private request?: Subscription;

  readonly status = signal<AgendaStatus>('none');
  readonly agenda = signal<AgendaResponse | null>(null);
  /** Session IDs on the agenda as the server last reported them. */
  private readonly onServer = computed(
    () => new Set(this.agenda()?.bookmarked.map((e) => e.sessionId) ?? []),
  );
  /** What a card shows before the server confirms: the pressed state of writes in flight or just refused. */
  private readonly pending = signal<ReadonlyMap<string, boolean>>(new Map());
  /** The session a write is in flight for, so its button waits. */
  readonly busy = signal<string | null>(null);
  /** One line for a polite live region: what just changed, or what failed. */
  readonly announcement = signal('');

  constructor() {
    effect(() => {
      const id = this.personas.selectedId();
      untracked(() => this.refresh(id));
    });
  }

  has(sessionId: string): boolean {
    return this.pending().get(sessionId) ?? this.onServer().has(sessionId);
  }

  /** The set a page can list: the server's, with the optimistic states on top. */
  readonly bookmarked = computed<ReadonlySet<string>>(() => {
    const ids = new Set(this.onServer());
    for (const [id, present] of this.pending()) {
      if (present) {
        ids.add(id);
      } else {
        ids.delete(id);
      }
    }
    return ids;
  });

  /** Load the persona's agenda. `keep` leaves the current list on screen while the new one arrives (after a write);
   *  otherwise the last persona's agenda is dropped first. No persona means no agenda and no request. */
  refresh(id: string = this.personas.selectedId(), keep = false): void {
    this.request?.unsubscribe();
    if (!keep) {
      this.agenda.set(null);
    }
    if (!id) {
      this.pending.set(new Map());
      this.status.set('none');
      return;
    }
    if (!keep) {
      this.status.set('loading');
    }
    this.request = this.portal.agenda(id).subscribe({
      next: (agenda) => {
        this.agenda.set(agenda);
        this.pending.set(new Map());
        this.status.set('done');
      },
      error: (error: unknown) => {
        this.agenda.set(null);
        this.pending.set(new Map());
        this.status.set(
          error instanceof HttpErrorResponse && error.status === 404 ? 'missing' : 'error',
        );
      },
    });
  }

  /** Add or remove one talk for the current persona. */
  toggle(sessionId: string, title: string): void {
    const profileId = this.personas.selectedId();
    if (!profileId || this.busy()) {
      return;
    }
    const adding = !this.has(sessionId);
    this.pending.update((m) => new Map(m).set(sessionId, adding));
    this.busy.set(sessionId);
    const write = adding
      ? this.portal.bookmark(profileId, sessionId)
      : this.portal.unbookmark(profileId, sessionId);
    write.subscribe({
      next: () => {
        this.busy.set(null);
        this.announcement.set(
          adding ? `Added “${title}” to My Agenda.` : `Removed “${title}” from My Agenda.`,
        );
        this.refresh(profileId, true);
      },
      error: () => {
        this.pending.update((m) => {
          const next = new Map(m);
          next.delete(sessionId);
          return next;
        });
        this.busy.set(null);
        this.announcement.set(
          adding
            ? `Couldn't add “${title}” to My Agenda right now.`
            : `Couldn't remove “${title}” from My Agenda right now.`,
        );
      },
    });
  }
}
