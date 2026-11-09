import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { App } from './app';
import { scrubUrlAttributes } from './telemetry';

function render() {
  TestBed.configureTestingModule({
    imports: [App],
    providers: [provideHttpClient(), provideHttpClientTesting()],
  });
  const fixture = TestBed.createComponent(App);
  fixture.detectChanges();
  return { fixture, el: fixture.nativeElement as HTMLElement, http: TestBed.inject(HttpTestingController) };
}

async function ask(el: HTMLElement, fixture: ReturnType<typeof render>['fixture'], text: string) {
  const input = el.querySelector<HTMLInputElement>('#ask-input')!;
  input.value = text;
  input.dispatchEvent(new Event('input'));
  el.querySelector('form')!.dispatchEvent(new Event('submit'));
  fixture.detectChanges();
  await fixture.whenStable();
}

describe('App shell', () => {
  it('has one h1, a skip link to main, and a labeled Ask input (A11Y-1)', () => {
    const { el } = render();
    expect(el.querySelectorAll('h1').length).toBe(1);
    expect(el.querySelector('a.skip-link')?.getAttribute('href')).toBe('#main');
    expect(el.querySelector('main#main')).not.toBeNull();
    expect(el.querySelector('label[for="ask-input"]')?.textContent).toContain('Ask about talks');
  });

  it('asks for words instead of searching for nothing', async () => {
    const { el, fixture, http } = render();
    await ask(el, fixture, '   ');
    http.expectNone('/api/v1/search');
    expect(el.querySelector('[role="alert"]')?.textContent).toContain('Type what you are looking for');
  });

  it('sends the query in the request body, never the URL (PRIV-1), and lists results', async () => {
    const { el, fixture, http } = render();
    await ask(el, fixture, 'kubernetes security');
    const req = http.expectOne('/api/v1/search');
    expect(req.request.method).toBe('POST');
    expect(req.request.urlWithParams).toBe('/api/v1/search');
    expect(req.request.body).toEqual({ query: 'kubernetes security' });
    req.flush({ results: [{ id: 's1', title: 'Policy as Code', event: 'KubeCon', startsAt: '' }], retrievalMode: 'hybrid' });
    fixture.detectChanges();
    expect(el.querySelector('.results h3')?.textContent).toContain('Policy as Code');
    expect(el.querySelector('[role="status"]')?.textContent).toContain('1 talk found');
  });

  it('shows a friendly message when search is down, not an error page', async () => {
    const { el, fixture, http } = render();
    await ask(el, fixture, 'observability');
    http.expectOne('/api/v1/search').flush('down', { status: 503, statusText: 'Service Unavailable' });
    fixture.detectChanges();
    expect(el.querySelector('[role="alert"]')?.textContent).toContain("Search isn't available right now");
  });
});

describe('scrubUrlAttributes (PRIV-1)', () => {
  it('removes query strings and fragments from URL attributes', () => {
    const out = scrubUrlAttributes({
      'url.full': 'https://x.example/search?q=quiet+room#top',
      'url.query': 'q=quiet+room',
      'page.url.fragment': 'top',
      'url.path': '/search',
    });
    expect(out['url.full']).toBe('https://x.example/search');
    expect(out['url.query']).toBe('REDACTED');
    expect(out['page.url.fragment']).toBe('REDACTED');
    expect(out['url.path']).toBe('/search');
  });
});
