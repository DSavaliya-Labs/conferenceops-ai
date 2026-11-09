import { Component, computed, inject, input } from '@angular/core';

import { AgendaService } from './agenda-state';
import { Icon } from './icons';
import { PersonaService } from './personas';

/** The quick add on every talk card: a toggle that says whether the talk is on the current persona's agenda.
 *  `aria-pressed` carries the state, the label names the talk for screen readers, and one polite status line
 *  announces the change (A11Y-1). Disabled while there is no persona or a write for this talk is in flight. */
@Component({
  selector: 'app-agenda-button',
  imports: [Icon],
  template: `
    <button
      type="button"
      class="btn agenda-toggle"
      [class.btn-outline]="!on()"
      [class.btn-primary]="on()"
      [attr.aria-pressed]="on()"
      [disabled]="!personas.selectedId() || agenda.busy() === sessionId()"
      (click)="agenda.toggle(sessionId(), title())"
    >
      <app-icon [name]="on() ? 'check' : 'bookmark'" [size]="16" />
      {{ on() ? 'In My Agenda' : 'Add to My Agenda' }}
      <span class="visually-hidden">: {{ title() }}</span>
    </button>
    <span class="visually-hidden" role="status" aria-live="polite">{{ status() }}</span>
  `,
  styles: `
    :host {
      display: inline-block;
    }
    .agenda-toggle {
      min-height: 44px;
    }
  `,
})
export class AgendaButton {
  readonly sessionId = input.required<string>();
  readonly title = input.required<string>();

  protected readonly agenda = inject(AgendaService);
  protected readonly personas = inject(PersonaService);
  protected readonly on = computed(() => this.agenda.has(this.sessionId()));
  /** Only this talk's change is announced here, so a page of cards never chatters. */
  protected readonly status = computed(() => {
    const line = this.agenda.announcement();
    return line.includes(`“${this.title()}”`) ? line : '';
  });
}
