import { Component, inject, output } from '@angular/core';

import { PersonaService } from './personas';

/** The "Viewing as" switch: a native select, so it works with the keyboard alone and keeps its focus when the persona
 *  changes (PLAN §4.1, A11Y-1). */
@Component({
  selector: 'app-persona-switcher',
  template: `
    <div class="persona">
      <label for="persona-select">Viewing as</label>
      <select
        id="persona-select"
        [disabled]="personas.status() !== 'ready' || personas.personas().length === 0"
        aria-describedby="persona-hint"
        (change)="onChange($event)"
      >
        <option value="" [selected]="!personas.selectedId()">Choose a demo persona</option>
        @for (persona of personas.personas(); track persona.id) {
          <option [value]="persona.id" [selected]="persona.id === personas.selectedId()">
            {{ persona.displayName }} · {{ persona.role
            }}{{ persona.isFictional ? ' · fictional' : '' }}
          </option>
        }
      </select>
      <p id="persona-hint" class="hint">
        @switch (personas.status()) {
          @case ('loading') {
            Loading the demo personas…
          }
          @case ('error') {
            Demo personas aren't available right now.
          }
          @default {
            @if (personas.personas().length === 0) {
              No demo personas yet.
            } @else {
              Fictional people from CloudBridge Summit. Anyone can pick any of them: a demo switch,
              not a sign-in.
            }
          }
        }
      </p>
    </div>
  `,
  styles: `
    .persona {
      margin: 0 0 1rem;
    }
    label {
      display: block;
      font-weight: 600;
      margin-bottom: 0.25rem;
    }
    select {
      font-size: 1rem;
      padding: 0.45rem;
      border: 2px solid #3f3f50;
      border-radius: 0.25rem;
      max-width: 100%;
    }
    .hint {
      color: #3f3f50;
      margin: 0.25rem 0 0;
      font-size: 0.9rem;
    }
    :focus-visible {
      outline: 3px solid #1d4ed8;
      outline-offset: 2px;
    }
  `,
})
export class PersonaSwitcher {
  protected readonly personas = inject(PersonaService);

  /** Emits after every change of persona, so the page can drop that persona's results. */
  readonly changed = output<string>();

  constructor() {
    this.personas.load();
  }

  protected onChange(event: Event): void {
    const id = (event.target as HTMLSelectElement).value;
    this.personas.select(id);
    this.changed.emit(id);
  }
}
