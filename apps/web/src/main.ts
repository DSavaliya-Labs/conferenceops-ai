import { bootstrapApplication } from '@angular/platform-browser';

import { App } from './app/app';
import { appConfig } from './app/app.config';
import { loadRuntimeConfig, startBrowserMonitoring } from './app/telemetry';

loadRuntimeConfig()
  .then(startBrowserMonitoring)
  .finally(() => bootstrapApplication(App, appConfig).catch((err) => console.error(err)));
