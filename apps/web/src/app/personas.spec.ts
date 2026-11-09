import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { PERSONA_STORAGE_KEY, PersonaService, defaultPersonaId } from './personas';
import { AISHA, PERSONAS, PROFILES_URL, TOMAS } from './test-support';

function setUp(): { service: PersonaService; http: HttpTestingController } {
  TestBed.configureTestingModule({
    providers: [provideHttpClient(), provideHttpClientTesting()],
  });
  return { service: TestBed.inject(PersonaService), http: TestBed.inject(HttpTestingController) };
}

describe('The persona a visit starts as', () => {
  beforeEach(() => localStorage.removeItem(PERSONA_STORAGE_KEY));
  afterEach(() => localStorage.removeItem(PERSONA_STORAGE_KEY));

  it('is Aisha, the hero of the demo, on a first visit, so no page waits for a pick', () => {
    const { service, http } = setUp();
    service.load();
    http.expectOne(PROFILES_URL).flush({ personas: PERSONAS });
    expect(service.selectedId()).toBe(AISHA.id);
    expect(service.selected()?.displayName).toBe('Aisha Rahman');
  });

  it('is the persona picked last time in this browser, when she is still listed', () => {
    localStorage.setItem(PERSONA_STORAGE_KEY, TOMAS.id);
    const { service, http } = setUp();
    service.load();
    http.expectOne(PROFILES_URL).flush({ personas: PERSONAS });
    expect(service.selectedId()).toBe(TOMAS.id);
  });

  it('falls back to the first persona when neither the stored one nor Aisha is listed', () => {
    localStorage.setItem(PERSONA_STORAGE_KEY, 'gone');
    const others = PERSONAS.filter((p) => p.id !== AISHA.id);
    expect(defaultPersonaId(others)).toBe(others[0].id);
    expect(defaultPersonaId([])).toBe('');
  });

  it('remembers a pick for the next visit, and keeps a pick made before the list arrived', () => {
    const { service, http } = setUp();
    service.select(TOMAS.id);
    expect(localStorage.getItem(PERSONA_STORAGE_KEY)).toBe(TOMAS.id);
    service.load();
    http.expectOne(PROFILES_URL).flush({ personas: PERSONAS });
    expect(service.selectedId()).toBe(TOMAS.id);
  });
});
