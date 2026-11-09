import { PERSONA_STORAGE_KEY } from './personas';
import {
  AISHA,
  PERSONAS,
  PROFILES_URL,
  TOMAS,
  View,
  click,
  key,
  loadPersonas,
  navigate,
  renderApp,
  settle,
  text,
  texts,
} from './test-support';

type AppView = View<import('./app').App>;

const trigger = (view: AppView) =>
  view.el.querySelector<HTMLButtonElement>('app-persona-menu .trigger')!;
const menu = (view: AppView) => view.el.querySelector('#persona-menu');
const items = (view: AppView) =>
  Array.from(view.el.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]'));

async function start(): Promise<AppView> {
  const view = renderApp();
  await loadPersonas(view);
  await navigate(view, '/talks');
  return view;
}

describe('Persona menu', () => {
  beforeEach(() => localStorage.removeItem(PERSONA_STORAGE_KEY));
  afterEach(() => localStorage.removeItem(PERSONA_STORAGE_KEY));

  it('is a menu button that says it is a fictional demo persona, closed by default', async () => {
    const view = await start();
    const button = trigger(view);
    expect(button.getAttribute('aria-haspopup')).toBe('menu');
    expect(button.getAttribute('aria-expanded')).toBe('false');
    expect(text(button)).toContain('Aisha Rahman'); // the demo's hero is the default persona
    expect(text(button).toLowerCase()).toContain('fictional demo persona');
    expect(text(view.el.querySelector('#persona-menu-hint'))).toContain('not a sign-in');
    expect(menu(view)).toBeNull();
  });

  it('lists the eight personas with their roles and companies, and focuses the first item on opening', async () => {
    const view = await start();
    click(trigger(view));
    await settle(view);
    expect(trigger(view).getAttribute('aria-expanded')).toBe('true');
    expect(trigger(view).getAttribute('aria-controls')).toBe('persona-menu');
    expect(menu(view)?.querySelector('[role="menu"]')?.getAttribute('aria-label')).toBe(
      'Viewing as',
    );
    expect(texts(view.el, '[role="menuitemradio"] .name')).toEqual(
      PERSONAS.map((p) => p.displayName),
    );
    expect(texts(view.el, '[role="menuitemradio"] .role')).toEqual([
      'Attendee',
      'Speaker · Quillon Labs',
      'Attendee',
      'Host',
      'Attendee',
      'Partner · Ferncrest Data',
      'Organizer',
      'Attendee',
    ]);
    expect(
      items(view)
        .filter((item) => item.getAttribute('aria-checked') === 'true')
        .map((item) => text(item.querySelector('.name'))),
    ).toEqual(['Aisha Rahman']); // the default persona is the checked one
    expect(document.activeElement).toBe(items(view)[0]);
  });

  it('works from the keyboard: arrows move, Home and End jump, Escape closes and returns focus', async () => {
    const view = await start();
    const button = trigger(view);
    button.focus();
    key(button, 'ArrowDown');
    await settle(view);
    expect(menu(view)).not.toBeNull();
    expect(document.activeElement).toBe(items(view)[0]);

    key(items(view)[0], 'ArrowDown');
    expect(document.activeElement).toBe(items(view)[1]);
    key(items(view)[1], 'ArrowUp');
    key(items(view)[0], 'ArrowUp'); // wraps to the last
    expect(document.activeElement).toBe(items(view)[7]);
    key(items(view)[7], 'Home');
    expect(document.activeElement).toBe(items(view)[0]);
    key(items(view)[0], 'End');
    expect(document.activeElement).toBe(items(view)[7]);

    key(items(view)[7], 'Escape');
    await settle(view);
    expect(menu(view)).toBeNull();
    expect(button.getAttribute('aria-expanded')).toBe('false');
    expect(document.activeElement).toBe(trigger(view));
  });

  it('picking a persona closes the menu, returns focus, shows her on the button, and later calls carry her ID', async () => {
    const view = await start();
    click(trigger(view));
    await settle(view);
    const tomas = items(view).find((item) => item.textContent?.includes('Tomás Vega'))!;
    tomas.click();
    await settle(view);

    expect(menu(view)).toBeNull();
    expect(document.activeElement).toBe(trigger(view));
    expect(text(trigger(view).querySelector('.name'))).toBe('Tomás Vega');
    expect(text(trigger(view).querySelector('.role'))).toBe('Attendee · fictional demo persona');
    expect(text(trigger(view).querySelector('.avatar'))).toBe('TV');

    click(trigger(view));
    await settle(view);
    const checked = items(view).filter((item) => item.getAttribute('aria-checked') === 'true');
    expect(checked.map((item) => text(item.querySelector('.name')))).toEqual(['Tomás Vega']);
    expect(document.activeElement).toBe(checked[0]); // opens on the current persona
    key(checked[0], 'Escape');
    await settle(view);

    await navigate(view, '/where-next');
    click(view.el.querySelector('#where-next-button'));
    await settle(view);
    const req = view.http.expectOne('/api/v1/recommendations/next');
    expect(req.request.headers.get('X-Demo-Profile-ID')).toBe(TOMAS.id);
    expect(req.request.headers.get('X-Demo-Profile-ID')).not.toBe(AISHA.id);
  });

  it('closes when focus or a click lands outside, without stealing focus back', async () => {
    const view = await start();
    click(trigger(view));
    await settle(view);
    expect(menu(view)).not.toBeNull();
    const input = view.el.querySelector<HTMLInputElement>('#ask-input')!;
    input.focus();
    items(view)[0]?.dispatchEvent(
      new FocusEvent('focusout', { bubbles: true, relatedTarget: input }),
    );
    await settle(view);
    expect(menu(view)).toBeNull();
    expect(document.activeElement).toBe(input);

    click(trigger(view));
    await settle(view);
    expect(menu(view)).not.toBeNull();
    document.body.click();
    await settle(view);
    expect(menu(view)).toBeNull();
  });

  it('says so when the personas are unavailable, and can try again', async () => {
    const view = renderApp();
    view.http
      .expectOne(PROFILES_URL)
      .flush('down', { status: 503, statusText: 'Service Unavailable' });
    await navigate(view, '/talks');
    click(trigger(view));
    await settle(view);
    expect(text(menu(view))).toContain("aren't available right now");
    expect(items(view).length).toBe(0);
    click(menu(view)!.querySelector('button'));
    await settle(view);
    view.http.expectOne(PROFILES_URL).flush({ personas: PERSONAS });
    await settle(view);
    expect(items(view).length).toBe(8);
  });
});
