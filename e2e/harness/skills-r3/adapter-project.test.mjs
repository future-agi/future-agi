import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const cli = fileURLToPath(import.meta.resolve('@playwright/test/cli'));
const playwright = fileURLToPath(import.meta.resolve('@playwright/test'));

test('ordinary CI excludes native flows; the separate project discovers them and fails closed without attestation', async () => {
  const scratch = await mkdtemp(join(tmpdir(), 'ctxhub-adapter-discovery-'));
  try {
    await writeFile(join(scratch, 'package.json'), '{"type":"module"}\n');
    const flows = join(scratch, 'flows/skills');
    await mkdir(flows, { recursive: true });
    for (const [file, name] of [['ordinary.spec.ts', 'ordinary-discovery-sentinel'], ['adapter.native.spec.ts', 'native-discovery-sentinel']]) {
      await writeFile(join(flows, file), `import { test } from ${JSON.stringify(playwright)}; test(${JSON.stringify(name)}, () => { throw new Error('Discovery must not execute tests'); });\n`);
    }
    for (const mode of ['ordinary', 'native']) {
      const source = join(root, 'e2e', mode === 'ordinary' ? 'playwright.config.ts' : 'playwright.native.config.ts');
      await writeFile(join(scratch, `${mode}.config.ts`), `import config from ${JSON.stringify(source)}; export default { ...config, testDir: ${JSON.stringify(scratch)} };\n`);
    }
    const env = { ...process.env };
    delete env.CTXHUB_NATIVE_ATTESTATION;
    delete env.CTXHUB_NATIVE_RELAY_TOKEN;
    const list = (mode, extra = {}) => spawnSync(process.execPath, [cli, 'test', '--config', join(scratch, `${mode}.config.ts`), '--list'],
      { cwd: root, env: { ...env, ...extra }, encoding: 'utf8', timeout: 30_000 });
    const ordinary = list('ordinary');
    assert.equal(ordinary.status, 0, ordinary.stderr);
    assert.match(ordinary.stdout, /ordinary-discovery-sentinel/);
    assert.doesNotMatch(ordinary.stdout, /native-discovery-sentinel/);
    const missing = list('native');
    assert.notEqual(missing.status, 0);
    assert.match(missing.stderr + missing.stdout, /Native runner unavailable/);
    // --list never executes globalSetup. This path is deliberately not an attestation
    // and proves only discovery, never a qualified native execution.
    const native = list('native', { CTXHUB_NATIVE_ATTESTATION: join(scratch, 'not-an-attestation.json') });
    assert.equal(native.status, 0, native.stderr);
    assert.match(native.stdout, /skills-native-qualification/);
    assert.match(native.stdout, /native-discovery-sentinel/);
    assert.doesNotMatch(native.stdout, /ordinary-discovery-sentinel/);
  } finally {
    await rm(scratch, { recursive: true, force: true });
  }
});
