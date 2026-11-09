import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { fileURLToPath } from 'node:url';
import { execFileSync, spawnSync } from 'node:child_process';
import { resolve } from 'node:path';
import { test, expect } from '@playwright/test';
import { parse } from 'yaml';
import { WalkReceipt } from '../lib/walk/receipt';
import { configSwitchVerdicts } from '../lib/walk/census';
import { walkBrowserSettings } from '../lib/walk/browsers';

const root = fileURLToPath(new URL('../../', import.meta.url));
const read = (p: string) => readFileSync(root + p, 'utf8');

for (const stack of ['standalone', 'distributed']) {
  test(`offline regression: ${stack} walk-mock opt-in preserves Compose extends paths`, () => {
    for (const enabled of ['0', '1']) {
      const config = JSON.parse(execFileSync(root + 'bin/e2e', ['compose', 'config', '--format', 'json'], {
        encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], env: { ...process.env,
          E2E_PROFILE: '', E2E_STACK: stack, E2E_GATEWAY_PROFILE: 'stock', E2E_GATEWAY_REPLICAS: '1',
          E2E_WALK_PINS: '1', E2E_WALK_MOCKS: enabled },
      }));
      for (const service of ['mcp-mock', 'otlp-sink']) {
        const build = config.services[service].build;
        expect(build.context, `${service} context, opt-in=${enabled}`).toBe(resolve(root, 'agentcc-gateway'));
        expect(resolve(build.context, build.dockerfile)).toBe(resolve(root, 'e2e/stack/walk-mocks/Dockerfile.go'));
      }
      for (const service of ['mock-certs', 'k2-mock', 'siem-sink']) {
        const build = config.services[service].build;
        expect(build.context, `${service} context, opt-in=${enabled}`).toBe(resolve(root, 'e2e/stack'));
        expect(resolve(build.context, build.dockerfile)).toBe(resolve(root, 'e2e/stack/walk-mocks/Dockerfile.node'));
      }
      for (const [service, file] of [['k2-mock', 'k2.mjs'], ['siem-sink', 'siem.mjs']]) {
        const mount = config.services[service].volumes.find((v: any) => v.target === `/srv/${file}`);
        expect(mount.source).toBe(resolve(root, 'e2e/stack/walk-mocks', file));
        expect(mount.read_only).toBe(true);
      }
    }
    // The deferred MCP+OTel profile uses these same already-extended services.
    const profiles = JSON.parse(read('e2e/stack/gateway-profiles.json'));
    expect(profiles['mcp-otel'].overlays).not.toContain('e2e/stack/docker-compose.walk-mocks.yml');
  });
}

const receiptFields = { profile: 'stock', mountedConfig: null,
  boundary: { attempts: [], ledger: [] } as any, browser: { name: 'chromium' } };
const syntheticEmail = ['receipt.actor', 'example.invalid'].join('@');
const syntheticOrg = ['01234567', '89ab', '4cde', '8fab', '0123456789ab'].join('-');

test('offline regression: the actual W00 metadata writes a value-free receipt', async ({}, info) => {
  // Exercise the spec's assignment, not a second hand-written copy of its data.
  // These actors deliberately carry raw identities that must be projected away.
  const source = read('e2e/flows/walks/default-off-census.spec.ts');
  const assignment = source.match(/^\s*receipt\.extra = \{ profile,.*;$/m)?.[0];
  expect(assignment).toBeDefined();
  const receipt = new WalkReceipt('W00', info);
  runInNewContext(assignment!, { receipt, profile: 'stock', A: syntheticOrg, B: syntheticOrg,
    scopes: { orgA: () => [{ role: 'owner', level: 10, actor: { email: syntheticEmail } },
      { role: 'viewer', level: 1, actor: { email: syntheticEmail } }] } });
  await receipt.write(receiptFields);
  const json = readFileSync(info.outputPath('walk-receipt.json'), 'utf8');
  expect(json).not.toMatch(/[\w.+-]+@[\w.-]+\.[a-z]{2,}/i);
  expect(json).not.toMatch(/[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}/i);
  expect(JSON.parse(json)).toMatchObject({ orgs: ['A', 'B'],
    roles: [{ role: 'owner', level: 10 }, { role: 'viewer', level: 1 }] });
});

for (const surface of ['extra', 'property', 'notes', 'verdict', 'boundary', 'browser', 'profile', 'title', 'screenshot', 'serialized'] as const) {
  test(`offline regression: receipt refuses sensitive ${surface} before write or attach`, async ({}, info) => {
    const receipt = new WalkReceipt('W00', info);
    const fields = structuredClone(receiptFields);
    if (surface === 'extra') receipt.extra = { nested: [{ value: syntheticEmail }] };
    if (surface === 'property') receipt.extra = { [syntheticOrg]: 1 };
    if (surface === 'notes') receipt.notes.push(syntheticEmail);
    if (surface === 'verdict') receipt.verdict({ feature: 'fixture', lane: 'api', where: 'fixture', verdict: 'OFF', detail: syntheticOrg });
    if (surface === 'boundary') fields.boundary.attempts.push({ origin: `https://${syntheticOrg}.invalid` });
    if (surface === 'browser') Object.assign(fields.browser, { accessToken: 'synthetic-only' });
    if (surface === 'profile') fields.profile = syntheticEmail;
    if (surface === 'title') receipt.extra.title = syntheticEmail;
    if (surface === 'screenshot') receipt.screenshots.push(info.outputPath(`${syntheticOrg}.png`));
    if (surface === 'serialized') receipt.extra = { nested: { toJSON: () => syntheticEmail } };
    const output = info.outputPath('walk-receipt.json');
    const attachments = info.attachments.length;
    await expect(receipt.write(fields)).rejects.toThrow(/^walk receipt refused: (email|UUID|key)-shaped value$/);
    expect(existsSync(output)).toBe(false);
    expect(info.attachments.length).toBe(attachments);
    // Refusal must not replace an earlier successful receipt either.
    writeFileSync(output, '{}');
    await expect(receipt.write(fields)).rejects.toThrow(/^walk receipt refused:/);
    expect(readFileSync(output, 'utf8')).toBe('{}');
    expect(info.attachments.length).toBe(attachments);
  });
}


for (const aborted of [false, true]) {
  test(`offline regression: real Iconify unloaded placeholder is counted (aborted=${aborted})`, async ({ page }, info) => {
    await page.route('**/*', route => route.abort());
    await page.setContent('<div id="root"></div><span></span><svg class="iconify"><path d="M0 0h1v1z"/></svg>');
    // Mount the installed package, not a guessed imitation of its DOM. CJS is
    // evaluated with the same React global as ReactDOM; no bundler or network.
    await page.addScriptTag({ content: read('frontend/node_modules/react/umd/react.production.min.js') });
    await page.addScriptTag({ content: read('frontend/node_modules/react-dom/umd/react-dom.production.min.js') });
    await page.addScriptTag({ content: `window.Iconify = (() => { const exports = {}; const require = () => window.React;\n${read('frontend/node_modules/@iconify/react/dist/iconify.js')}\nreturn exports; })();` });
    await page.evaluate(() => {
      const w = window as any;
      w.iconRoot = w.ReactDOM.createRoot(document.getElementById('root'));
      w.iconRoot.render(w.React.createElement(w.Iconify.Icon,
        { icon: 'e2e-unloaded:missing', className: 'iconify' }));
    });
    await expect(page.locator('#root > span')).toHaveCount(1);
    expect(await page.locator('#root > span').getAttribute('class')).toBeNull();
    const receipt = new WalkReceipt('control', info);
    await receipt.icons(page, 'control', 'inline', aborted);
    expect(receipt.emptyIcons).toEqual([{ context: 'control', page: 'inline', count: 1,
      attribution: aborted ? 'ASSETS-PENDING' : 'PRODUCT-BUG' }]);
    // Loaded real icon plus unrelated empty spans must not be counted.
    await page.evaluate(() => {
      const w = window as any;
      w.iconRoot.render(w.React.createElement(w.Iconify.Icon, { icon: { body: '<path d="M0 0h1v1z"/>' } }));
    });
    await expect(page.locator('#root svg')).toHaveCount(1);
    const loaded = new WalkReceipt('loaded', info);
    await loaded.icons(page, 'control', 'inline', aborted);
    expect(loaded.emptyIcons).toEqual([]);
  });
}

for (const [field, value] of [['enabled', true], ['servers', { x: {} }], ['virtual_servers', [{ id: 'x' }]]] as const) {
  test(`offline regression: MCP ${field} ON is not hidden by a missing banner`, () => {
    for (const lane of ['storage', 'gateway'] as const) {
      const row = configSwitchVerdicts({ mcp: { [field]: value } }, lane, 'org').find(r => r.feature === `mcp.${field}`);
      expect(row?.verdict).toBe('ON');
    }
  });
}

test('offline regression: receipts do not claim browser flags absent from actual launch options', () => {
  // Main playwright.config.ts supplies none, regardless of the strict env default.
  expect(walkBrowserSettings('chromium', {} as any)).toMatchObject({ strict: false, chromiumArgs: [] });
});

test('offline regression: legacy r7alt refusal keeps its exact exit and message', () => {
  const result = spawnSync('bash', [root + 'bin/e2e', 'compose', 'config'], {
    encoding: 'utf8', env: { ...process.env, E2E_STACK: 'distributed', E2E_GATEWAY_PROFILE: 'r7alt' },
  });
  expect(result.status).toBe(1);
  expect(result.stderr.trim()).toBe('E2E_GATEWAY_PROFILE=r7alt runs on the Standalone stack only (E2E_STACK=standalone)');
});

test('offline regression: Distributed walk overrides all restarting services for strict admission', () => {
  const base = parse(read('docker-compose.distributed.yml'));
  const walk = parse(read('e2e/stack/docker-compose.walk-pins.distributed.yml'));
  for (const [name, service] of Object.entries<any>(base.services)) {
    if (service.restart && service.restart !== 'no') expect(walk.services[name]?.restart, name).toBe('no');
    if (service.image?.startsWith('futureagi/future-agi:')) expect(walk.services[name]?.environment, name).toMatchObject({
      FUTURE_AGI_LICENSE_URL: 'http://127.0.0.1:9', OLLAMA_HOST: '127.0.0.1:9', LITELLM_LOCAL_MODEL_COST_MAP: 'True' });
  }
});


test('offline regression: compatibility rehearsal preserves legacy scope with actual loopback browser flags', async () => {
  const legacy = (await import('../playwright.config')).default;
  const compat = (await import('../playwright.compat.config')).default;
  expect(compat.testMatch).toEqual(legacy.testMatch);
  expect(compat.workers).toBe(legacy.workers);
  expect(compat.grepInvert).toEqual(legacy.grepInvert);
  expect(walkBrowserSettings('chromium', compat.use?.launchOptions)).toMatchObject({ strict: true });
  expect(walkBrowserSettings('chromium', legacy.use?.launchOptions)).toMatchObject({ strict: false });
});


test('offline regression: one-shot Django fixtures flush results and exit despite SDK threads', async () => {
  const { djangoFixtureCommand } = await import('../lib/django-fixture');
  const thread = 'import threading\nthreading.Thread(target=lambda: threading.Event().wait(), daemon=False).start()\n';
  const ok = spawnSync('python3', ['-I', '-c', djangoFixtureCommand(thread + 'print("fixture committed")')],
    { encoding: 'utf8', timeout: 3000 });
  expect(ok.error).toBeUndefined();
  expect(ok.status).toBe(0);
  expect(ok.stdout.trim()).toBe('fixture committed');
  const bad = spawnSync('python3', ['-I', '-c', djangoFixtureCommand(thread + 'raise ValueError("fixture failed")')],
    { encoding: 'utf8', timeout: 3000 });
  expect(bad.error).toBeUndefined();
  expect(bad.status).toBe(1);
  expect(bad.stderr).toContain('ValueError: fixture failed');
});


test('offline regression: Redis WRONGTYPE with a trailing blank line still probes the hash', async () => {
  const { RedisProbe } = await import('../lib/redis-probe');
  const org = '11111111-1111-4111-8111-111111111111';
  const key = `mc:{${org}}:acct`;
  const probe = new RedisProbe([org], args => {
    if (args[0] === 'SCAN') return args[3] === `mc:{${org}}*` ? `0\n${key}\n` : '0\n\n';
    if (args[0] === 'PTTL') return '-1\n';
    if (args[0] === 'GET') return 'WRONGTYPE Operation against a key holding the wrong kind of value\n\n';
    if (args[0] === 'HGETALL') return 'spent\n42\n';
    throw new Error('unexpected read');
  }, '');
  expect(probe.snapshot(org)).toEqual([{ key, pttl: -1, kind: 'hash', value: { spent: '42' } }]);
});
