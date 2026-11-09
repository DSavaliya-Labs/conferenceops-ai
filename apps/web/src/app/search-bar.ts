import { Component, ElementRef, inject, signal, viewChild } from '@angular/core';
import { Router } from '@angular/router';

import { DictationButton } from './dictation-button';
import { Icon } from './icons';
import { PersonaService } from './personas';
import { SearchService } from './search';
import { WhereNextService } from './where-next';

/** The Ask bar, centered in the app bar: one field, two questions. Search answers "which talk matches my
 *  interests?"; "Where next?" answers "which talk can I actually go to?". Submitting opens the matching page; the
 *  field keeps focus, so the attendee can refine, and the page's live region says what came back (A11Y-1).
 *  The dictation microphone sits inside the field, next to the input; its status line wraps under the field. */
@Component({
  selector: 'app-search-bar',
  imports: [DictationButton, Icon],
  template: `
    <form class="search" role="search" (submit)="submit($event)" novalidate>
      <label for="ask-input" class="visually-hidden">Ask about talks</label>
      <div class="field" [class.invalid]="validation()">
        <app-icon name="search" class="lead-icon" />
        <input
          id="ask-input"
          #askInput
          type="search"
          name="query"
          autocomplete="off"
          enterkeyhint="search"
          placeholder="Ask about talks, in your own words"
          [value]="search.query()"
          (input)="onInput($event)"
          [attr.aria-invalid]="validation() ? 'true' : null"
          [attr.aria-describedby]="validation() ? 'ask-error' : 'ask-hint'"
        />
        <app-dictation-button (transcribed)="onDictated($event)" />
        <button type="submit" class="btn btn-primary" [disabled]="search.status() === 'searching'">
          <app-icon name="search" [size]="18" />
          <span class="label">Search</span>
        </button>
        <button type="button" class="btn btn-accent" (click)="whereNext()">
          <app-icon name="compass" [size]="18" />
          <span class="label">Where next?</span>
        </button>
      </div>
      <p id="ask-hint" class="visually-hidden">
        Describe what you want to learn. Results open on the Find talks page. Where next opens
        suggestions for the chosen persona.
      </p>
      @if (validation()) {
        <p id="ask-error" class="error" role="alert">{{ validation() }}</p>
      }
    </form>
  `,
  styles: `
    :host {
      display: block;
      min-width: 0;
    }
    .search {
      display: grid;
      gap: var(--space-1);
    }
    .field {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: var(--space-2);
      min-height: 3rem;
      padding: 0.25rem 0.25rem 0.25rem var(--space-3);
      border: 1.5px solid var(--line-strong);
      border-radius: var(--radius-full);
      background: var(--surface);
      transition:
        border-color var(--dur-fast) var(--ease),
        box-shadow var(--dur-fast) var(--ease);
    }
    .field:hover {
      border-color: var(--ink-2);
    }
    .field:focus-within {
      border-color: var(--brand);
      box-shadow: 0 0 0 3px var(--brand-soft);
    }
    .field.invalid {
      border-color: var(--danger);
    }
    .lead-icon {
      color: var(--ink-3);
    }
    input {
      flex: 1 1 6rem;
      min-width: 0;
      min-height: 2.5rem;
      padding: 0 var(--space-2);
      border: 0;
      background: transparent;
      color: var(--ink);
      font-size: var(--text-md);
    }
    input::placeholder {
      color: var(--ink-3);
      opacity: 1;
    }
    input:focus-visible {
      outline: none; /* the field around it shows focus */
    }
    input::-webkit-search-cancel-button {
      appearance: none;
    }
    .btn {
      border-radius: var(--radius-full);
      min-width: var(--touch);
      padding-inline: var(--space-3);
      flex: none;
    }
    .error {
      padding: 0 var(--space-4);
      font-size: var(--text-sm);
    }
    @media (max-width: 599px) {
      .label {
        position: absolute;
        width: 1px;
        height: 1px;
        overflow: hidden;
        clip-path: inset(50%);
        white-space: nowrap;
      }
      .btn {
        width: var(--touch);
        padding: 0;
      }
    }
  `,
})
export class SearchBar {
  protected readonly search = inject(SearchService);
  private readonly whereNextService = inject(WhereNextService);
  private readonly personas = inject(PersonaService);
  private readonly router = inject(Router);
  private readonly askInput = viewChild.required<ElementRef<HTMLInputElement>>('askInput');

  protected readonly validation = signal('');

  protected onInput(event: Event): void {
    this.search.query.set((event.target as HTMLInputElement).value);
    this.validation.set('');
  }

  protected submit(event: Event): void {
    event.preventDefault();
    const query = this.search.query().trim();
    if (!query) {
      this.validation.set(
        'Type what you are looking for, for example “Kubernetes security for beginners”.',
      );
      return;
    }
    this.validation.set('');
    this.search.run(query);
    void this.router.navigateByUrl('/talks');
  }

  /** The other question, for the chosen persona. The page shows the answer, or asks for a persona first. */
  protected whereNext(): void {
    this.validation.set('');
    this.whereNextService.ask(this.personas.selected());
    void this.router.navigateByUrl('/where-next');
  }

  /** Dictated text lands in the field for review: focus moves there, and nothing is searched until the attendee
   *  presses Search (PLAN §9.2). */
  protected onDictated(text: string): void {
    this.search.query.set(text);
    this.validation.set('');
    this.askInput().nativeElement.focus();
  }
}
