import { appendFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import type { BrowserContext } from '@playwright/test';

/** Record destinations, never request paths, bodies or authentication values. */
export async function lockToLoopback(context: BrowserContext, directory: string) {
  if (!directory) throw new Error('The editor proof requires persisted network records');
  mkdirSync(directory, { recursive: true });
  const record = (kind: string, hostname: string, port: number) => {
    appendFileSync(join(directory, `browser-${process.pid}.jsonl`),
      `${JSON.stringify({ kind, layer: 'browser.route', hostname, port })}\n`, { mode: 0o600 });
  };
  record('installed', 'localhost', 0);
  await context.route(url => /^https?:$/.test(url.protocol) &&
    !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname), async route => {
    const destination = new URL(route.request().url());
    record('refused', destination.hostname, Number(destination.port || (destination.protocol === 'https:' ? 443 : 80)));
    await route.abort('blockedbyclient');
  });
}
