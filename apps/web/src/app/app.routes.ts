import { Routes } from '@angular/router';

export const routes: Routes = [
  // The captioned replay of a concluded CloudBridge session; `?t=<seconds>` opens it at a cited moment.
  {
    path: 'sessions/:id/replay',
    loadComponent: () => import('./replay-view').then((m) => m.ReplayViewComponent),
  },
];
