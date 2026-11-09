import { Component, input } from '@angular/core';

export type IconName =
  | 'search'
  | 'compass'
  | 'bookmark'
  | 'calendar'
  | 'access'
  | 'activity'
  | 'menu'
  | 'close'
  | 'chevron-down'
  | 'check'
  | 'external'
  | 'pin'
  | 'clock'
  | 'refresh'
  | 'user'
  | 'info'
  | 'alert'
  | 'spark'
  | 'arrow-right';

/** The app's icons, inline SVG with one stroke weight, so they take the text color and ship with no font or image
 *  request. Every icon is decorative: the text next to it carries the meaning (A11Y-1). */
@Component({
  selector: 'app-icon',
  template: `
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox="0 0 24 24"
      [attr.width]="size()"
      [attr.height]="size()"
      fill="none"
      stroke="currentColor"
      stroke-width="1.75"
      stroke-linecap="round"
      stroke-linejoin="round"
    >
      @switch (name()) {
        @case ('search') {
          <circle cx="11" cy="11" r="7" />
          <path d="m21 21-4.3-4.3" />
        }
        @case ('compass') {
          <circle cx="12" cy="12" r="9" />
          <path d="m16.2 7.8-2.1 6.3-6.3 2.1 2.1-6.3z" />
        }
        @case ('bookmark') {
          <path d="M6 4h12v17l-6-4.5L6 21z" />
        }
        @case ('calendar') {
          <rect x="3" y="5" width="18" height="16" rx="2" />
          <path d="M16 3v4M8 3v4M3 10h18" />
        }
        @case ('access') {
          <circle cx="12" cy="4.5" r="1.5" />
          <path d="M4 9.5c5.3 1.3 10.7 1.3 16 0M12 10v5M8.5 21 12 15l3.5 6" />
        }
        @case ('activity') {
          <path d="M3 12h4l3-8 4 16 3-8h4" />
        }
        @case ('menu') {
          <path d="M4 7h16M4 12h16M4 17h16" />
        }
        @case ('close') {
          <path d="m6 6 12 12M18 6 6 18" />
        }
        @case ('chevron-down') {
          <path d="m6 9 6 6 6-6" />
        }
        @case ('check') {
          <path d="m5 12 5 5L20 7" />
        }
        @case ('external') {
          <path d="M14 4h6v6M20 4l-9 9M18 13v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h6" />
        }
        @case ('pin') {
          <path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21z" />
          <circle cx="12" cy="9.5" r="2.5" />
        }
        @case ('clock') {
          <circle cx="12" cy="12" r="9" />
          <path d="M12 7v5l3 2" />
        }
        @case ('refresh') {
          <path d="M21 12a9 9 0 1 1-2.6-6.4M21 3v6h-6" />
        }
        @case ('user') {
          <circle cx="12" cy="8" r="4" />
          <path d="M4 21a8 8 0 0 1 16 0" />
        }
        @case ('info') {
          <circle cx="12" cy="12" r="9" />
          <path d="M12 11v5M12 8h.01" />
        }
        @case ('alert') {
          <path d="M12 3 2 20h20zM12 9v5M12 17h.01" />
        }
        @case ('spark') {
          <path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" />
        }
        @case ('arrow-right') {
          <path d="M5 12h14M13 6l6 6-6 6" />
        }
      }
    </svg>
  `,
  styles: `
    :host {
      display: inline-flex;
      flex: none;
      line-height: 0;
      vertical-align: middle;
    }
    svg {
      display: block;
    }
  `,
})
export class Icon {
  readonly name = input.required<IconName>();
  readonly size = input(20);
}
