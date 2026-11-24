import { expect, request, test as base, type Route } from '@playwright/test';
import { backendRequestContext, firstHopOptions } from './backend-transport';
import { E2E } from './env';

export { expect };
export const test = base.extend({
  request: async ({ request, baseURL }, use) => {
    await use(backendRequestContext(request, E2E.apiUrl, baseURL));
  },
});

export async function newBackendRequest(options: Parameters<typeof request.newContext>[0] = {}) {
  return backendRequestContext(await request.newContext(options), E2E.apiUrl, options.baseURL);
}

// route.fetch is a Node API request, not a browser continuation. Leave the intercepted request alone.
export function fetchRoute(route: Route) {
  return route.fetch(firstHopOptions(route.request(), E2E.apiUrl, {}));
}
