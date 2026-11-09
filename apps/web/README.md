# Web: the attendee portal

The Angular 22 app (zoneless, signals, Vitest) that the public URL serves. It talks to the APIs through the same
origin: nginx proxies `/api` in the container, and `ng serve` proxies it to the live AKS deployment
(`proxy.conf.json`), so the app runs locally against real data with no backend on the laptop.

```bash
npm ci
npm start                    # http://localhost:4200, /api proxied to the public URL
npx ng test --watch=false    # unit and accessibility tests (Vitest, jsdom)
npx ng build                 # production build into dist/web/browser
```

## Layout

| Path | What it is |
|---|---|
| `src/app/app.ts`, `app.html`, `app.css` | The shell: the app bar (brand, the Ask bar, the persona menu), the left navigation (`NAV_LINKS`; a menu button below 880 px), the routed page and the footer. Focus moves to the page after a navigation link, not after a search |
| `src/app/app.routes.ts` | One lazy route per feature, each with its page title |
| `src/app/search-bar.ts` | The Ask bar: one field, the Dictate, Search and Where next buttons. Search opens Find talks; Where next opens the suggestions for the chosen persona |
| `src/app/persona-menu.ts` | The account menu: a menu button with the persona's initials, name and role; arrow keys, Home, End, Escape and focus return (A11Y-1). The pick travels as `X-Demo-Profile-ID` on later requests (AUTHZ-1) |
| `src/app/pages/` | `find-talks`, `where-next-page`, `agenda`, `events`, `accessibility`, `operations`: each with honest loading, empty and error states (a 503 says the feature isn't available and offers a retry) |
| `src/app/search.ts`, `where-next.ts`, `portal.ts`, `personas.ts` | The HTTP calls and the state the pages show. Query text travels in request bodies, never URLs (PRIV-1) |
| `src/app/session-card.ts`, `where-next-cards.ts`, `icons.ts` | The talk card (event badge, venue time from the API, room, level, topics), the suggestion cards with reasons and sourced access facts (ACCESS-1), and the inline SVG icons |
| `src/app/dictation-button.ts` | The microphone in the Ask bar (PLAN §9.2) |
| `src/app/telemetry.ts` | The Dash0 Web SDK, started from `/config.json`; URL query strings are scrubbed (PRIV-1) |
| `src/styles.css` | The design tokens (color, type scale, spacing, radius, elevation) for light and dark, the base styles, and the shared primitives (`.btn`, `.card`, `.badge`, `.callout`) |
| `src/app/test-support.ts` | What the specs share: the rendered shell with the router and a fake HTTP backend, and the demo personas |

No component library and no web font: hand-written CSS on the tokens, system fonts, inline icons. The only
runtime dependency beyond Angular is the Dash0 Web SDK.
