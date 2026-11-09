import { Routes } from '@angular/router';

/** Every feature on the platform has a page and a URL, so any screen can be linked to. Pages load lazily and are
 *  preloaded right after the shell (app.config.ts). A new feature mounts with one entry here and one in the nav
 *  (app.ts). */
export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'talks' },
  {
    path: 'talks',
    title: 'Find talks · ConferenceOps AI',
    loadComponent: () => import('./pages/find-talks').then((m) => m.FindTalksPage),
  },
  {
    path: 'where-next',
    title: 'Where should I go next? · ConferenceOps AI',
    loadComponent: () => import('./pages/where-next-page').then((m) => m.WhereNextPage),
  },
  {
    path: 'agenda',
    title: 'My agenda · ConferenceOps AI',
    loadComponent: () => import('./pages/agenda').then((m) => m.AgendaPage),
  },
  {
    path: 'events',
    title: 'Events · ConferenceOps AI',
    loadComponent: () => import('./pages/events').then((m) => m.EventsPage),
  },
  {
    path: 'accessibility',
    title: 'Accessibility at the venue · ConferenceOps AI',
    loadComponent: () => import('./pages/accessibility').then((m) => m.AccessibilityPage),
  },
  {
    path: 'operations',
    title: 'How this platform runs · ConferenceOps AI',
    loadComponent: () => import('./pages/operations').then((m) => m.OperationsPage),
  },
  { path: '**', redirectTo: 'talks' },
];
