import { test as base, expect } from './fixtures';

// Browser acceptance for synthetic link evaluation never fetches remote assets.
// The page addresses the stack through its published host ports only.
export function isLocalBrowserUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return ['http:', 'https:', 'ws:', 'wss:'].includes(url.protocol) &&
      ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  } catch {
    return false;
  }
}

export const test = base.extend({
  context: async ({ context }, use, testInfo) => {
    const blocked: string[] = [];
    await context.route('**/*', async route => {
      if (isLocalBrowserUrl(route.request().url())) await route.continue();
      else {
        blocked.push(route.request().url());
        await route.abort('blockedbyclient');
      }
    });
    try { await use(context); }
    finally {
      await testInfo.attach('blocked-external-browser-urls', {
        contentType: 'application/json', body: JSON.stringify(blocked),
      });
    }
  },
});
export { expect };
