import { HttpClient } from '@angular/common/http';
import { Service, computed, inject, signal } from '@angular/core';

/** The header that says which persona a request acts as (AUTHZ-1). */
export const DEMO_PROFILE_HEADER = 'X-Demo-Profile-ID';

/** A selectable demo persona, as core-api lists it: public fields only (PRIV-1). Mirrors `PersonaCard` in
 *  contracts/openapi.json (API-1); the web image builds from apps/web alone, so the type is restated here. */
export interface Persona {
  id: string;
  displayName: string;
  role: string;
  company?: string | null;
  interests: string[];
  isFictional: boolean;
}

interface PersonaList {
  personas: Persona[];
}

export type PersonaStatus = 'loading' | 'ready' | 'error';

const ROLE_LABELS: Record<string, string> = {
  attendee: 'Attendee',
  speaker: 'Speaker',
  partner: 'Partner',
  organizer: 'Organizer',
  host: 'Host',
};

/** "Attendee", "Speaker · Quillon Labs": the role, and the company when there is one. */
export function roleLine(persona: Persona): string {
  const role = ROLE_LABELS[persona.role] ?? persona.role;
  return persona.company ? `${role} · ${persona.company}` : role;
}

/** Up to two initials for the avatar: "Aisha Rahman" → "AR", "Tomás Vega" → "TV". */
export function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const first = parts[0]?.[0] ?? '';
  const last = parts.length > 1 ? (parts[parts.length - 1][0] ?? '') : '';
  return (first + last).toUpperCase();
}

/** Who the app acts as. The server resolves everything else from the ID in `X-Demo-Profile-ID` (AUTHZ-1): this is a
 *  demonstration switch, not a sign-in. */
@Service()
export class PersonaService {
  private readonly http = inject(HttpClient);

  readonly personas = signal<Persona[]>([]);
  readonly status = signal<PersonaStatus>('loading');
  readonly selectedId = signal('');
  readonly selected = computed(
    () => this.personas().find((p) => p.id === this.selectedId()) ?? null,
  );

  load(): void {
    this.status.set('loading');
    this.http.get<PersonaList>('/api/v1/demo/profiles').subscribe({
      next: (response) => {
        this.personas.set(response.personas);
        this.status.set('ready');
      },
      error: () => {
        this.personas.set([]);
        this.status.set('error');
      },
    });
  }

  select(id: string): void {
    this.selectedId.set(id);
  }
}
