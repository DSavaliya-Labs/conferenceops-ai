import {
  Component,
  DestroyRef,
  ElementRef,
  computed,
  effect,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';
import { ActivatedRoute } from '@angular/router';
import { Subscription } from 'rxjs';

import { ReplayService, ReplayView, clock } from './replay';

type Status = 'loading' | 'done' | 'missing' | 'error';

/** Only same-origin media under /media/ is ever handed to the player: the API's own fixture paths (SEC-1). */
export function isTrustedMediaPath(url: string): boolean {
  return url.startsWith('/media/') && !url.startsWith('//') && !url.includes('..');
}

/** The captioned replay of a concluded CloudBridge session (PLAN §4.3, §9.3): the video with its captions on, a
 *  pause control, and the transcript panel, which highlights the line being spoken and can be paused on its own.
 *  A processing transcript says so and shows no text. Nothing here is a live region (A11Y-1). */
@Component({
  selector: 'app-replay-view',
  templateUrl: './replay-view.html',
  styleUrl: './replay-view.css',
})
export class ReplayViewComponent {
  private readonly route = inject(ActivatedRoute);
  private readonly service = inject(ReplayService);
  private readonly sanitizer = inject(DomSanitizer);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly heading = viewChild<ElementRef<HTMLElement>>('heading');
  private readonly player = viewChild<ElementRef<HTMLVideoElement>>('player');
  private request?: Subscription;

  protected readonly status = signal<Status>('loading');
  protected readonly view = signal<ReplayView | null>(null);
  protected readonly startAt = signal(0);
  protected readonly playing = signal(false);
  /** The "Pause updates" control: while off, the highlighted line stays where it is (A11Y-1). */
  protected readonly following = signal(true);
  protected readonly highlighted = signal<number | null>(null);
  protected readonly clock = clock;
  private readonly reducedMotion =
    typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

  /** The video's src is a resource URL for Angular, so a trusted path is wrapped; the track's src is a plain URL. */
  protected readonly mediaUrl = computed<SafeResourceUrl | null>(() =>
    this.trust(this.view()?.media.url),
  );
  protected readonly captionsUrl = computed<string | null>(() => {
    const url = this.view()?.media.captionsUrl;
    return url && isTrustedMediaPath(url) ? url : null;
  });
  protected readonly published = computed(() => this.view()?.transcript.status === 'published');

  constructor() {
    const destroyRef = inject(DestroyRef);
    this.route.paramMap
      .pipe(takeUntilDestroyed(destroyRef))
      .subscribe((params) => this.load(params.get('id') ?? ''));
    this.route.queryParamMap.pipe(takeUntilDestroyed(destroyRef)).subscribe((params) => {
      const seconds = Number(params.get('t'));
      this.startAt.set(Number.isFinite(seconds) && seconds >= 0 ? seconds : 0);
      this.seekIfReady();
    });
    // When a citation opens the replay, focus lands on its heading, not at the top of the page (A11Y-1).
    effect(() => this.heading()?.nativeElement.focus());
  }

  private trust(url: string | undefined): SafeResourceUrl | null {
    return url && isTrustedMediaPath(url)
      ? this.sanitizer.bypassSecurityTrustResourceUrl(url)
      : null;
  }

  private load(sessionId: string): void {
    this.request?.unsubscribe();
    this.status.set('loading');
    this.view.set(null);
    this.highlighted.set(null);
    this.request = this.service.replay(sessionId).subscribe({
      next: (view) => {
        this.view.set(view);
        this.status.set('done');
      },
      error: (err: { status?: number }) =>
        this.status.set(err?.status === 404 ? 'missing' : 'error'),
    });
  }

  /** The `?t=` moment, applied once the player knows its duration. */
  protected onLoadedMetadata(): void {
    this.seekIfReady();
  }

  private seekIfReady(): void {
    const video = this.player()?.nativeElement;
    if (video && video.readyState >= 1 && this.startAt() > 0) {
      video.currentTime = this.startAt();
    }
  }

  protected togglePlayback(): void {
    const video = this.player()?.nativeElement;
    if (!video) {
      return;
    }
    if (video.paused) {
      void video.play()?.catch(() => this.playing.set(false));
    } else {
      video.pause();
    }
  }

  protected onPlay(): void {
    this.playing.set(true);
  }

  protected onPause(): void {
    this.playing.set(false);
  }

  protected toggleFollowing(): void {
    this.following.update((on) => !on);
  }

  protected seek(seconds: number): void {
    const video = this.player()?.nativeElement;
    if (video) {
      video.currentTime = seconds;
    }
  }

  protected onTimeUpdate(): void {
    const video = this.player()?.nativeElement;
    const cues = this.view()?.transcript.cues ?? [];
    if (!video || !this.following()) {
      return;
    }
    const time = video.currentTime;
    const index = cues.findIndex((cue) => time >= cue.start && time < cue.end);
    if (index === this.highlighted()) {
      return;
    }
    this.highlighted.set(index >= 0 ? index : null);
    if (index >= 0 && !this.reducedMotion) {
      const line = this.host.nativeElement.querySelector<HTMLElement>(`#cue-${index}`);
      line?.scrollIntoView?.({ block: 'nearest' }); // never smooth: reduced motion is respected above (A11Y-1)
    }
  }
}
