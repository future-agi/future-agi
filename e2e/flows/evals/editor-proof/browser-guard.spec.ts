import { readFileSync, readdirSync } from 'node:fs';
import { createServer } from 'node:http';
import { join } from 'node:path';
import { test, expect } from '../../../lib/trace-hygiene/fixture';
import { lockToLoopback } from './browser-guard';

test('editor browser guard persists refusals without a runner-specific directory', async ({ context }, testInfo) => {
  const previous = process.env.EVAL_GUARD_DIR;
  delete process.env.EVAL_GUARD_DIR;
  const directory = testInfo.outputPath('network-records');
  const server = createServer((_request, response) => {
    response.setHeader('content-type', 'text/html');
    response.end('<h1>Local editor behaviour reached</h1>');
  });
  try {
    // Reflect.apply also exercises the previous one-argument implementation.
    await Reflect.apply(lockToLoopback, undefined, [context, directory]);
    await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
    const address = server.address();
    if (!address || typeof address === 'string') throw new Error('Missing local listener');
    const page = await context.newPage();
    await page.goto(`http://127.0.0.1:${address.port}`);
    await expect(page.getByRole('heading')).toHaveText('Local editor behaviour reached');
    for (const destination of ['http://editor-control.invalid:8123/', 'https://editor-control.invalid/']) {
      await expect(page.goto(destination)).rejects.toThrow();
    }
    const records = readdirSync(directory).flatMap(name =>
      readFileSync(join(directory, name), 'utf8').trim().split('\n').map(line => JSON.parse(line)));
    expect(records).toEqual([
      { kind: 'installed', layer: 'browser.route', hostname: 'localhost', port: 0 },
      { kind: 'refused', layer: 'browser.route', hostname: 'editor-control.invalid', port: 8123 },
      { kind: 'refused', layer: 'browser.route', hostname: 'editor-control.invalid', port: 443 },
    ]);
  } finally {
    if (previous === undefined) delete process.env.EVAL_GUARD_DIR;
    else process.env.EVAL_GUARD_DIR = previous;
    await new Promise<void>(resolve => server.close(() => resolve()));
  }
});
