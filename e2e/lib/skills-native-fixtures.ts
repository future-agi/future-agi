import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { test as base, expect } from './fixtures';

// One native-runtime service and one provider fixture namespace per local
// standalone stack. Do not replace another worker's model rules mid-job.
// flock releases on process exit (including a crashed Playwright worker).
export const test = base.extend<{ nativeNamespace: string }>({
  nativeNamespace: async ({}, use) => {
    if (process.platform !== 'linux') throw new Error('Native fixtures require the cloud Linux stack');
    const lease = spawn('flock', ['--exclusive', '--timeout', '300', '/tmp/futureagi-e2e-skills-native.lock',
      'sh', '-c', 'echo acquired; cat >/dev/null'], { stdio: ['pipe', 'pipe', 'ignore'] });
    const exit = new Promise<number | null>((resolve, reject) => {
      lease.once('error', reject); lease.once('exit', resolve);
    });
    const output = createInterface({ input: lease.stdout });
    try {
      const acquired = await Promise.race([output[Symbol.asyncIterator]().next().then(line => line.value === 'acquired'), exit.then(() => false)]);
      if (!acquired) throw new Error('Standalone native runtime is busy');
      await use('e2e-skills-native-runtime-fixture');
    } finally {
      output.close(); lease.stdin.end();
      await exit;
    }
  },
});
export { expect };
