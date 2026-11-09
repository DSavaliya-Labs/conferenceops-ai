import { DOCUMENT, Component, ElementRef, inject, signal, viewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import {
  NavigationCancel,
  NavigationEnd,
  NavigationError,
  NavigationSkipped,
  Router,
  RouterLink,
  RouterLinkActive,
  RouterOutlet,
} from '@angular/router';
import { filter } from 'rxjs';

import { IconName, Icon } from './icons';
import { PersonaMenu } from './persona-menu';
import { PersonaService } from './personas';
import { SearchBar } from './search-bar';
import { WhereNextService } from './where-next';

export interface NavLink {
  path: string;
  label: string;
  icon: IconName;
}

/** The left navigation, one entry per feature on the platform. A new feature adds one line here and one route in
 *  app.routes.ts. */
export const NAV_LINKS: NavLink[] = [
  { path: '/talks', label: 'Find talks', icon: 'search' },
  { path: '/where-next', label: 'Where next', icon: 'compass' },
  { path: '/agenda', label: 'My agenda', icon: 'bookmark' },
  { path: '/community', label: 'Community', icon: 'user' },
  { path: '/events', label: 'Events', icon: 'calendar' },
  { path: '/accessibility', label: 'Accessibility at the venue', icon: 'access' },
  { path: '/operations', label: 'How this platform runs', icon: 'activity' },
];

/** The attendee portal's shell: a top app bar (the product name, the Ask bar centered, the persona menu at the
 *  right), the left navigation (a disclosure behind a menu button on narrow screens), the routed page, and the
 *  footer with the platform's disclosures. Landmarks, a skip link and focus management make it keyboard-first
 *  (A11Y-1). */
@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, Icon, PersonaMenu, SearchBar],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {
  protected readonly personas = inject(PersonaService);
  private readonly whereNext = inject(WhereNextService);
  private readonly document = inject(DOCUMENT);
  private readonly navToggle = viewChild.required<ElementRef<HTMLButtonElement>>('navToggle');

  protected readonly links = NAV_LINKS;
  protected readonly navOpen = signal(false);
  /** Set by a navigation link, so a page change moves focus to the page, not a search from the Ask bar. */
  private focusMainAfterNavigation = false;

  constructor() {
    this.personas.load();
    inject(Router)
      .events.pipe(
        filter(
          (event) =>
            event instanceof NavigationEnd ||
            event instanceof NavigationSkipped ||
            event instanceof NavigationCancel ||
            event instanceof NavigationError,
        ),
        takeUntilDestroyed(),
      )
      .subscribe((event) => {
        this.navOpen.set(false);
        const focus =
          this.focusMainAfterNavigation &&
          (event instanceof NavigationEnd || event instanceof NavigationSkipped);
        this.focusMainAfterNavigation = false;
        if (focus) {
          this.document.getElementById('main')?.focus();
        }
      });
  }

  protected onNavLink(): void {
    this.focusMainAfterNavigation = true;
  }

  protected toggleNav(): void {
    this.navOpen.update((open) => !open);
  }

  /** Escape closes the narrow-screen navigation and puts focus back on the button that opened it. */
  protected onNavKeydown(event: KeyboardEvent): void {
    if (event.key === 'Escape' && this.navOpen()) {
      event.preventDefault();
      this.navOpen.set(false);
      this.navToggle().nativeElement.focus();
    }
  }

  /** PLAN §4.1: a persona switch drops the last persona's suggestions, so they never show under the new one. */
  protected onPersonaChanged(): void {
    this.whereNext.clear();
  }
}
