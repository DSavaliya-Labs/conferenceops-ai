import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { AskResponse } from './ask';
import { AskAnswer } from './ask-answer';

const SESSION_ID = '2f1c8f7e-4f1a-4a51-9b1a-0c7a5b2d9e11';
const QUESTION = 'what did the agent get wrong about the database';

function answer(source: 'model' | 'passages', citations = 1): AskResponse {
  return {
    answer:
      source === 'model'
        ? 'The agent blamed a healthy database; a config push had emptied the connection pool.'
        : 'From “AI SRE in Practice” (Daniel Kowalczyk): “The database was fine.”',
    answerSource: source,
    citations: Array.from({ length: citations }, (_, i) => ({
      chunkId: `t:1:${i}`,
      sessionId: SESSION_ID,
      sessionTitle: 'AI SRE in Practice: What Our Agent Got Wrong',
      speaker: 'Daniel Kowalczyk',
      startSeconds: 18.98 + 10 * i,
      endSeconds: 27.3 + 10 * i,
      replayPath: `/sessions/${SESSION_ID}/replay?t=${(18.98 + 10 * i).toFixed(1)}`,
      excerpt: 'The database was fine. What the agent had seen was a jump in connection errors.',
      label: 'Simulated excerpt',
    })),
    retrievalMode: 'hybrid',
    event: 'CloudBridge Summit 2026',
    isFictional: true,
  };
}

function render(question: string) {
  TestBed.configureTestingModule({
    imports: [AskAnswer],
    providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
  });
  const fixture = TestBed.createComponent(AskAnswer);
  fixture.componentRef.setInput('question', question);
  fixture.detectChanges();
  return {
    fixture,
    el: fixture.nativeElement as HTMLElement,
    http: TestBed.inject(HttpTestingController),
  };
}

async function respond(
  fixture: ReturnType<typeof render>['fixture'],
  http: HttpTestingController,
  body: AskResponse | null,
  status = 200,
) {
  const request = http.expectOne('/api/v1/ask');
  if (body) {
    request.flush(body);
  } else {
    request.flush('down', { status, statusText: 'Service Unavailable' });
  }
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();
  return request;
}

describe('AskAnswer', () => {
  it('shows nothing and asks nothing without a question', () => {
    const { el, http } = render('');
    http.expectNone('/api/v1/ask');
    expect(el.querySelector('section')).toBeNull();
  });

  it('sends the question in the request body, never the URL (PRIV-1), and shows the cited answer with replay links', async () => {
    const { fixture, el, http } = render(QUESTION);
    const request = await respond(fixture, http, answer('model', 2));

    expect(request.request.method).toBe('POST');
    expect(request.request.urlWithParams).toBe('/api/v1/ask');
    expect(request.request.body).toEqual({ query: QUESTION });

    const text = el.textContent ?? '';
    expect(text).toContain('Fictional demo event');
    expect(text).toContain('Simulated excerpt');
    expect(el.querySelector('.answer')?.textContent).toContain(
      'The agent blamed a healthy database',
    );
    expect(el.querySelector('.source')?.textContent).toContain(
      'Written by the model from the cited passages only',
    );
    const links = el.querySelectorAll<HTMLAnchorElement>('.citations a');
    expect(links.length).toBe(2);
    expect(links[0].getAttribute('href')).toBe(`/sessions/${SESSION_ID}/replay?t=18.98`);
    expect(links[0].textContent).toContain('Daniel Kowalczyk');
    expect(links[0].textContent).toContain('0:18–0:27');
    expect(el.querySelector('.citations q')?.textContent).toContain('The database was fine.');
    expect(el.querySelector('[role="status"]')?.textContent).toContain(
      'Answer ready, 2 cited moments.',
    );
  });

  it('labels a deterministic fallback as not model-written (AI-1)', async () => {
    const { fixture, el, http } = render(QUESTION);
    await respond(fixture, http, answer('passages'));
    expect(el.querySelector('.source')?.textContent).toContain('Not model-written');
    expect(el.querySelector('.answer')?.textContent).toContain('The database was fine.');
    expect(el.querySelectorAll('.citations a').length).toBe(1);
  });

  it('says when no published transcript answers, with no citations to show', async () => {
    const { fixture, el, http } = render(QUESTION);
    await respond(fixture, http, {
      ...answer('passages', 0),
      answer:
        "No published CloudBridge transcript covers this. A transcript that is still processing can't be cited.",
    });
    expect(el.querySelector('.answer')?.textContent).toContain('still processing');
    expect(el.querySelector('.citations')).toBeNull();
    expect(el.querySelector('[role="status"]')?.textContent).toContain(
      'No answer from the archive.',
    );
  });

  it('shows a friendly message when the archive is down, not an error page', async () => {
    const { fixture, el, http } = render(QUESTION);
    await respond(fixture, http, null, 503);
    expect(el.querySelector('[role="alert"]')?.textContent).toContain(
      "The archive isn't answering right now",
    );
    expect(el.querySelector('.answer')).toBeNull();
  });
});
