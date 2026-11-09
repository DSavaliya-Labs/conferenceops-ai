import {
  Component,
  ElementRef,
  afterRenderEffect,
  computed,
  inject,
  output,
  signal,
  untracked,
  viewChild,
  viewChildren,
} from '@angular/core';

import { Icon } from './icons';
import { Persona, PersonaService, initialsOf, roleLine } from './personas';

/** The account menu at the top right, in the style of a social platform's: an avatar with initials, the persona's
 *  name and role, and a menu of the eight fictional demo personas. It follows the menu-button pattern (A11Y-1):
 *  Enter, Space and the arrow keys open it, arrows and Home/End move, Enter or Space picks, Escape closes and focus
 *  returns to the button. A pick stays in memory only: the server learns it from `X-Demo-Profile-ID` on each later
 *  request (AUTHZ-1). It is a demo switch, not a sign-in. */
@Component({
  selector: 'app-persona-menu',
  imports: [Icon],
  host: { '(document:click)': 'onDocumentClick($event)' },
  template: `
    <div class="persona" (keydown)="onKeydown($event)" (focusout)="onFocusOut($event)">
      <button
        #trigger
        type="button"
        class="trigger"
        aria-haspopup="menu"
        [attr.aria-expanded]="open()"
        [attr.aria-controls]="open() ? 'persona-menu' : null"
        aria-describedby="persona-menu-hint"
        (click)="toggle()"
      >
        <span class="avatar" [class.empty]="!selected()" aria-hidden="true">
          @if (selected()) {
            {{ initials() }}
          } @else {
            <app-icon name="user" [size]="18" />
          }
        </span>
        <span class="who">
          <span class="name">{{ selected()?.displayName ?? 'Choose a persona' }}</span>
          <span class="role">{{ roleText() }}</span>
        </span>
        <app-icon name="chevron-down" [size]="16" />
      </button>
      <p id="persona-menu-hint" class="visually-hidden">
        Viewing as. Fictional people from CloudBridge Summit: a demo switch, not a sign-in.
      </p>
      @if (open()) {
        <div id="persona-menu" class="popover">
          <p class="note">
            Fictional demo personas. Anyone can pick any of them: a demo switch, not a sign-in.
          </p>
          @switch (personas.status()) {
            @case ('loading') {
              <p class="state">Loading the demo personas…</p>
            }
            @case ('error') {
              <p class="state">Demo personas aren't available right now.</p>
              <button type="button" class="btn btn-outline retry" (click)="personas.load()">
                Try again
              </button>
            }
            @default {
              @if (personas.personas().length === 0) {
                <p class="state">No demo personas yet.</p>
              } @else {
                <ul role="menu" aria-label="Viewing as">
                  @for (persona of personas.personas(); track persona.id) {
                    <li role="none">
                      <button
                        #item
                        type="button"
                        role="menuitemradio"
                        tabindex="-1"
                        [attr.aria-checked]="persona.id === personas.selectedId()"
                        (click)="select(persona)"
                      >
                        <span class="avatar small" aria-hidden="true">{{
                          initialsOf(persona.displayName)
                        }}</span>
                        <span class="who">
                          <span class="name">{{ persona.displayName }}</span>
                          <span class="role">{{ roleLine(persona) }}</span>
                        </span>
                        @if (persona.id === personas.selectedId()) {
                          <app-icon name="check" [size]="18" />
                        }
                      </button>
                    </li>
                  }
                </ul>
              }
            }
          }
        </div>
      }
    </div>
  `,
  styles: `
    .persona {
      position: relative;
    }
    .trigger {
      display: inline-flex;
      align-items: center;
      gap: var(--space-2);
      min-height: var(--touch);
      max-width: 100%;
      padding: 0.25rem 0.5rem 0.25rem 0.25rem;
      border: 1.5px solid var(--line);
      border-radius: var(--radius-full);
      background: var(--surface);
      color: var(--ink);
      cursor: pointer;
      text-align: left;
      transition:
        background-color var(--dur-fast) var(--ease),
        border-color var(--dur-fast) var(--ease),
        box-shadow var(--dur-fast) var(--ease);
    }
    .trigger:hover,
    .trigger[aria-expanded='true'] {
      background: var(--surface-2);
      border-color: var(--line-strong);
    }
    .avatar {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex: none;
      width: 2.25rem;
      height: 2.25rem;
      border-radius: var(--radius-full);
      background: var(--brand);
      color: var(--on-brand);
      font-weight: 700;
      font-size: var(--text-sm);
      letter-spacing: 0.02em;
    }
    .avatar.empty {
      background: var(--surface-3);
      color: var(--ink-2);
    }
    .avatar.small {
      width: 2rem;
      height: 2rem;
      font-size: var(--text-xs);
      background: var(--brand-soft);
      color: var(--brand-strong);
    }
    .who {
      display: grid;
      line-height: 1.25;
      min-width: 0;
    }
    .name {
      font-weight: 600;
      font-size: var(--text-sm);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .role {
      font-size: var(--text-xs);
      color: var(--ink-3);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    @media (max-width: 599px) {
      .trigger .who {
        position: absolute;
        width: 1px;
        height: 1px;
        overflow: hidden;
        clip-path: inset(50%);
        white-space: nowrap;
      }
    }
    .popover {
      position: absolute;
      right: 0;
      top: calc(100% + 0.5rem);
      z-index: 60;
      width: min(22rem, calc(100vw - 2rem));
      padding: var(--space-2);
      border: 1px solid var(--line);
      border-radius: calc(var(--radius-md) + var(--space-2));
      background: var(--surface);
      box-shadow: var(--shadow-3);
      animation: pop var(--dur-base) var(--ease);
    }
    @keyframes pop {
      from {
        opacity: 0;
        transform: translateY(-4px);
      }
    }
    .note,
    .state {
      margin: 0;
      padding: var(--space-2) var(--space-3);
      font-size: var(--text-xs);
      color: var(--ink-3);
    }
    .state {
      font-size: var(--text-sm);
      color: var(--ink-2);
    }
    .retry {
      margin: 0 var(--space-3) var(--space-2);
    }
    ul {
      list-style: none;
      margin: 0;
      padding: 0;
      display: grid;
      gap: 2px;
    }
    [role='menuitemradio'] {
      display: flex;
      align-items: center;
      gap: var(--space-3);
      width: 100%;
      min-height: var(--touch);
      padding: 0.375rem var(--space-3);
      border: 0;
      border-radius: var(--radius-md);
      background: transparent;
      color: var(--ink);
      cursor: pointer;
      text-align: left;
      transition: background-color var(--dur-fast) var(--ease);
    }
    [role='menuitemradio']:hover,
    [role='menuitemradio']:focus-visible {
      background: var(--surface-2);
    }
    [role='menuitemradio'][aria-checked='true'] {
      background: var(--brand-soft);
    }
    [role='menuitemradio'] app-icon {
      margin-left: auto;
      color: var(--brand);
    }
    [role='menuitemradio'] .who {
      flex: 1;
    }
    [role='menuitemradio'] .name {
      font-size: var(--text-md);
      font-weight: 500;
    }
    [role='menuitemradio'][aria-checked='true'] .name {
      font-weight: 700;
    }
  `,
})
export class PersonaMenu {
  protected readonly personas = inject(PersonaService);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly trigger = viewChild.required<ElementRef<HTMLButtonElement>>('trigger');
  private readonly items = viewChildren<ElementRef<HTMLButtonElement>>('item');

  /** Emits after every change of persona, so the shell can drop the last persona's results. */
  readonly changed = output<string>();

  protected readonly open = signal(false);
  /** The item to focus once the menu has rendered. */
  private readonly pendingFocus = signal<number | null>(null);
  protected readonly selected = this.personas.selected;
  protected readonly initials = computed(() => initialsOf(this.selected()?.displayName ?? ''));
  protected readonly roleText = computed(() => {
    const persona = this.selected();
    return persona ? `${roleLine(persona)} · fictional demo persona` : 'Fictional demo persona';
  });
  protected readonly initialsOf = initialsOf;
  protected readonly roleLine = roleLine;

  constructor() {
    afterRenderEffect(() => {
      const index = this.pendingFocus();
      const items = this.items();
      if (index === null || items.length === 0) {
        return;
      }
      untracked(() => this.pendingFocus.set(null));
      items[Math.min(index, items.length - 1)].nativeElement.focus();
    });
  }

  protected toggle(): void {
    if (this.open()) {
      this.close(true);
    } else {
      this.show(this.selectedIndex());
    }
  }

  protected select(persona: Persona): void {
    this.personas.select(persona.id);
    this.changed.emit(persona.id);
    this.close(true);
  }

  protected onKeydown(event: KeyboardEvent): void {
    const isOpen = this.open();
    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault();
        if (isOpen) {
          this.move(1);
        } else {
          this.show(this.selectedIndex());
        }
        break;
      case 'ArrowUp':
        event.preventDefault();
        if (isOpen) {
          this.move(-1);
        } else {
          this.show(this.personas.selectedId() ? this.selectedIndex() : this.count() - 1);
        }
        break;
      case 'Home':
        if (isOpen) {
          event.preventDefault();
          this.focusItem(0);
        }
        break;
      case 'End':
        if (isOpen) {
          event.preventDefault();
          this.focusItem(this.count() - 1);
        }
        break;
      case 'Escape':
        if (isOpen) {
          event.preventDefault();
          this.close(true);
        }
        break;
      case 'Tab':
        if (isOpen) {
          this.close(false);
        }
        break;
    }
  }

  protected onFocusOut(event: FocusEvent): void {
    const next = event.relatedTarget;
    if (this.open() && next instanceof Node && !this.host.nativeElement.contains(next)) {
      this.close(false);
    }
  }

  protected onDocumentClick(event: Event): void {
    if (
      this.open() &&
      event.target instanceof Node &&
      !this.host.nativeElement.contains(event.target)
    ) {
      this.close(false);
    }
  }

  private show(index: number): void {
    this.open.set(true);
    this.pendingFocus.set(Math.max(index, 0));
  }

  private close(returnFocus: boolean): void {
    this.open.set(false);
    this.pendingFocus.set(null);
    if (returnFocus) {
      this.trigger().nativeElement.focus();
    }
  }

  private count(): number {
    return this.items().length;
  }

  private selectedIndex(): number {
    return Math.max(
      this.personas.personas().findIndex((p) => p.id === this.personas.selectedId()),
      0,
    );
  }

  private focusItem(index: number): void {
    const items = this.items();
    if (items.length > 0) {
      items[Math.min(Math.max(index, 0), items.length - 1)].nativeElement.focus();
    }
  }

  private move(delta: number): void {
    const items = this.items();
    if (items.length === 0) {
      return;
    }
    const current = items.findIndex(
      (item) => item.nativeElement === this.host.nativeElement.ownerDocument.activeElement,
    );
    const next =
      current === -1
        ? delta > 0
          ? 0
          : items.length - 1
        : (current + delta + items.length) % items.length;
    items[next].nativeElement.focus();
  }
}
