/* Helpers the specs share: a rendered shell with the router and a fake HTTP backend, and the fictional demo personas
 * as core-api lists them. Not part of the app build (tsconfig.app.json excludes it). */

import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Type } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';

import { App } from './app';
import { routes } from './app.routes';
import { Persona } from './personas';

export const PROFILES_URL = '/api/v1/demo/profiles';

/** The eight personas, as the live core-api lists them (names and companies are fictional). */
export const PERSONAS: Persona[] = [
  {
    id: '9e93bdf5-9c94-57eb-a683-02c6d8aff44f',
    displayName: 'Aisha Rahman',
    role: 'attendee',
    company: null,
    interests: ['AI SRE', 'Kubernetes agents', 'observability'],
    isFictional: true,
  },
  {
    id: '26990aa7-bebc-59c3-b8cc-391c54ff79d8',
    displayName: 'Daniel Kowalczyk',
    role: 'speaker',
    company: 'Quillon Labs',
    interests: ['AI SRE'],
    isFictional: true,
  },
  {
    id: 'a79ccdcb-c406-551c-9bdb-135ca332819f',
    displayName: 'Grace Mensah',
    role: 'attendee',
    company: null,
    interests: ['OpenTelemetry', 'Kubernetes basics'],
    isFictional: true,
  },
  {
    id: 'f04d3cf3-93b6-5306-81b3-4c5e3a09c533',
    displayName: 'Jordan Lee',
    role: 'host',
    company: null,
    interests: ['community'],
    isFictional: true,
  },
  {
    id: '7fc8a44b-cbea-5042-8f70-8173ba1360d2',
    displayName: 'Lin Zhao',
    role: 'attendee',
    company: null,
    interests: ['security', 'eBPF'],
    isFictional: true,
  },
  {
    id: 'fc9af310-86ad-5a13-a21e-ec5e93b06028',
    displayName: 'Priyanka Desai',
    role: 'partner',
    company: 'Ferncrest Data',
    interests: ['developer experience'],
    isFictional: true,
  },
  {
    id: '78a8b421-818c-5497-af24-54b07ecd093e',
    displayName: 'Sam Rivera',
    role: 'organizer',
    company: null,
    interests: ['operations'],
    isFictional: true,
  },
  {
    id: '33ced7fb-7a20-5477-a398-78b8baa46ce1',
    displayName: 'Tomás Vega',
    role: 'attendee',
    company: null,
    interests: ['platform engineering', 'GitOps'],
    isFictional: true,
  },
];
export const AISHA = PERSONAS[0];
export const TOMAS = PERSONAS[7];

export interface View<T> {
  fixture: ComponentFixture<T>;
  el: HTMLElement;
  http: HttpTestingController;
  router: Router;
}

/** Render a component with the app's routes and a fake HTTP backend. */
export function render<T>(component: Type<T>): View<T> {
  TestBed.configureTestingModule({
    imports: [component],
    providers: [provideRouter(routes), provideHttpClient(), provideHttpClientTesting()],
  });
  const fixture = TestBed.createComponent(component);
  fixture.detectChanges();
  return {
    fixture,
    el: fixture.nativeElement as HTMLElement,
    http: TestBed.inject(HttpTestingController),
    router: TestBed.inject(Router),
  };
}

/** The whole shell: app bar, navigation, routed page and footer. */
export function renderApp(): View<App> {
  return render(App);
}

export async function settle<T>(view: View<T>): Promise<void> {
  view.fixture.detectChanges();
  await view.fixture.whenStable();
  view.fixture.detectChanges();
}

export async function navigate<T>(view: View<T>, url: string): Promise<void> {
  await view.router.navigateByUrl(url);
  await settle(view);
}

/** Answer the shell's request for the personas. */
export async function loadPersonas<T>(
  view: View<T>,
  personas: Persona[] = PERSONAS,
): Promise<void> {
  view.http.expectOne(PROFILES_URL).flush({ personas });
  await settle(view);
}

/** Answer (or clear) the shared agenda service's request, which every persona pick sends. */
export function flushAgenda<T>(view: View<T>, bookmarked: string[] = []): void {
  for (const req of view.http.match('/api/v1/me/agenda')) {
    req.flush({
      profileId: AISHA.id,
      displayName: AISHA.displayName,
      bookmarked: bookmarked.map((sessionId) => ({ sessionId })),
      attended: [],
    });
  }
}

export function key(target: Element, key: string): void {
  target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
}

export function click(target: Element | null): void {
  (target as HTMLElement).click();
}

export function text(el: Element | null): string {
  return (el?.textContent ?? '').replace(/\s+/g, ' ').trim();
}

export function texts(el: ParentNode, selector: string): string[] {
  return Array.from(el.querySelectorAll(selector)).map(text);
}

/** Pick a persona through the account menu, as a person would: open it and activate the item. */
export async function choosePersona<T>(view: View<T>, name: string): Promise<void> {
  click(view.el.querySelector('app-persona-menu .trigger'));
  await settle(view);
  const item = Array.from(
    view.el.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]'),
  ).find((button) => button.textContent?.includes(name));
  if (!item) {
    throw new Error(`No persona named ${name} in the menu`);
  }
  item.click();
  await settle(view);
}
