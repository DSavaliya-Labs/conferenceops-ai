import { render, settle, text, texts } from '../test-support';
import { OperationsPage } from './operations';

describe('How this platform runs', () => {
  it('walks through the loop with a link to the evidence for each step', async () => {
    const view = render(OperationsPage);
    view.http
      .expectOne('/config.json')
      .flush({ environment: 'aks', version: '11634b6d56a2cb450c74d12395627426f6195742' });
    await settle(view);
    expect(text(view.el.querySelector('h1'))).toBe('How this platform runs');
    expect(texts(view.el, '.loop h3')).toEqual([
      'A pull request opens',
      'A merge to main deploys',
      'Attendees use the app, Dash0 watches',
      'A check fails',
      'Agent0 investigates and opens a fix pull request',
      'Radar’s AI SRE investigates the cluster',
    ]);
    const hrefs = Array.from(view.el.querySelectorAll<HTMLAnchorElement>('a')).map((a) =>
      a.getAttribute('href'),
    );
    expect(hrefs).toContain('https://github.com/DSavaliya-Labs/conferenceops-ai');
    expect(hrefs).toContain('https://github.com/DSavaliya-Labs/conferenceops-ai/issues/1');
    expect(hrefs).toContain('http://conferenceops-ai.westus3.cloudapp.azure.com');
    expect(text(view.el)).toContain('ConferenceOps: attendee impact');
    for (const link of view.el.querySelectorAll('a[target="_blank"]')) {
      expect(link.getAttribute('rel')).toContain('noopener');
      expect(text(link)).toContain('opens in a new tab');
    }
  });

  it('shows the running version and environment from the runtime config, and never the browser token (SEC-1)', async () => {
    const view = render(OperationsPage);
    view.http.expectOne('/config.json').flush({
      environment: 'aks',
      version: '11634b6d56a2cb450c74d12395627426f6195742',
      dash0Token: 'auth_secret_value_that_must_stay_out_of_the_page',
    });
    await settle(view);
    const deployment = view.el.querySelector('.deployment')!;
    expect(text(deployment)).toContain('aks');
    const version = deployment.querySelector<HTMLAnchorElement>('a.mono')!;
    expect(text(version)).toContain('11634b6d56a2');
    expect(version.getAttribute('href')).toBe(
      'https://github.com/DSavaliya-Labs/conferenceops-ai/commit/11634b6d56a2cb450c74d12395627426f6195742',
    );
    expect(view.el.innerHTML).not.toContain('auth_secret_value');
  });

  it('says when the runtime config is not reported', async () => {
    const view = render(OperationsPage);
    view.http.expectOne('/config.json').flush('missing', { status: 404, statusText: 'Not Found' });
    await settle(view);
    expect(texts(view.el, '.deployment dd')[1]).toBe('not reported');
    expect(texts(view.el, '.deployment dd')[2]).toBe('not reported');
  });
});
