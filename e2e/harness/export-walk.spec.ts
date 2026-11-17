import { readFileSync } from 'node:fs';
import { test, expect } from '@playwright/test';
import { parse } from 'yaml';
import { EXPORT_ENDPOINTS, storageUserPolicy } from '../lib/export-sinks';
import { assertStorageMounts, fingerprint, safeJson } from '../lib/walk/export-destinations';
import * as adapters from '../lib/walk/export-destinations';
import { HARNESS_PATHS, declaredOverlay, WalkReceipt } from '../lib/walk/receipt';
import { connectionErrorStatus, verified } from '../lib/walk/export-sync';

// Offline, cloud-only. Existing fixtures/interfaces remain unchanged; these
// checks exercise the additive W17 safety seam and value-free attachments.
test('offline W17 endpoints are exactly the carried H15 local destinations', () => {
  const config = parse(readFileSync(new URL('../stack/docker-compose.e2e.yml', import.meta.url), 'utf8'));
  for (const service of ['backend', 'worker']) {
    expect(Object.fromEntries(Object.entries(config.services[service].environment)
      .filter(([name]) => name.startsWith('INTEGRATIONS_')))).toEqual(EXPORT_ENDPOINTS);
  }
  expect(config.services['storage-sink'].ports).toBeUndefined();
  expect(config.services['storage-sink'].healthcheck.test).toEqual([
    'CMD-SHELL', 'mc ready sink && mc stat sink/e2e-exports >/dev/null',
  ]);
  expect(config.networks['e2e-public'].internal).toBe(false);
  // synthetic test value — synthetic bucket/prefix input
  expect(storageUserPolicy('b', 'p').Statement).toEqual([
    // synthetic test value — synthetic policy resource expectation
    { Effect: 'Allow', Action: ['s3:PutObject', 's3:DeleteObject'], Resource: ['arn:aws:s3:::b/p/*'] },
  ]);
});

test('offline W17 seeded identities and query receipts contain only prefixed digests', async ({}, info) => {
  // synthetic test value — synthetic identity fragments
  const raw = { id: ['11111111', '1111', '4111', '8111', '111111111111'].join('-'),
    // synthetic test value — synthetic address and credential fragments
    address: ['fixture', 'example.invalid'].join('@'), token: ['sk', 'synthetic-only'].join('-') };
  expect(() => safeJson(raw)).toThrow('walk receipt refused');
  const json = safeJson({ identity: fingerprint(raw), scopeSet: fingerprint([raw.id]) });
  expect(json).not.toContain(raw.id);
  const receipt = new WalkReceipt('W17', info);
  // synthetic test value — synthetic receipt feature
  verified(receipt, 'fixture result', 'storage', raw);
  expect(() => safeJson(receipt.verdicts)).not.toThrow();
  expect(receipt.verdicts[0].detail).toEqual({ result: 'pass', queriedDigest: fingerprint(raw) });
  // synthetic test value — negative fingerprint comparison input
  expect(fingerprint({ ...raw, changed: true })).not.toBe(fingerprint(raw));
  // synthetic test value — synthetic overlay commit and path fixture
  const overlay = { from: 'b'.repeat(40), paths: ['e2e', 'bin/e2e', 'bin/lib'], identical: true };
  expect(() => safeJson({ overlay })).not.toThrow();
  // synthetic test value — negative extra-overlay-field fixture
  expect(() => safeJson({ overlay: { ...overlay, unexpected: 'e'.repeat(40) } })).toThrow();
});

test('offline W17 accepts the MinIO data volume but refuses host/source mounts', () => {
  // synthetic test value — empty mounts fixture
  expect(() => assertStorageMounts([])).not.toThrow();
  // synthetic test value — synthetic mount fixture
  const mount = { Type: 'volume', Source: '/var/lib/docker/volumes/fixture/_data', Destination: '/data', RW: true };
  expect(() => assertStorageMounts([mount])).not.toThrow();
  // synthetic test value — negative mount variants
  for (const change of [{ Type: 'bind' }, { Destination: '/srv' }, { RW: false }]) {
    expect(() => assertStorageMounts([{ ...mount, ...change }])).toThrow();
  }
});

test('offline product identity excludes only harness and the two explicitly overlaid mock programs', () => {
  expect(HARNESS_PATHS).toEqual(['e2e', 'bin/e2e', 'bin/lib',
    'agentcc-gateway/cmd/agentcc-mcpmock', 'agentcc-gateway/cmd/agentcc-otlpmock']);
  expect(HARNESS_PATHS.some(p => p.includes('*'))).toBe(false);
});

test('offline W17 failure controls are scoped to each destination and reversible', async () => {
  const calls: unknown[] = [];
  // synthetic test value — synthetic sink double
  const sink = {
    // synthetic test value — synthetic stopped-call recording
    stopped: async (...args: unknown[]) => { calls.push(['stopped', ...args]); },
    // synthetic test value — synthetic revoked-call recording
    revoked: async (...args: unknown[]) => { calls.push(['revoked', ...args]); },
  };
  // synthetic test value — synthetic storage-call recording
  const runStorage = (args: string[]) => { calls.push(['storage', ...args]); };
  const toggle = (adapters as any).setDestinationFailure;
  expect(typeof toggle).toBe('function');
  // synthetic test value — synthetic destination matrix
  for (const kind of ['datadog', 'mixpanel', 'posthog', 's3']) {
    // synthetic test value — synthetic failure toggle matrix
    for (const down of [true, false]) {
      // synthetic test value — synthetic credential/channel
      await toggle({ kind, credential: 'fixture-credential', channel: 'fixture-channel',
        // synthetic test value — synthetic storage user
        user: { accessKeyId: 'fixture-user' } }, sink, down, runStorage);
    }
  }
  expect(calls).toEqual([
    // synthetic test value — synthetic expected credential calls
    ['revoked', 'fixture-credential', true], ['revoked', 'fixture-credential', false],
    // synthetic test value — synthetic expected credential calls
    ['revoked', 'fixture-credential', true], ['revoked', 'fixture-credential', false],
    // synthetic test value — synthetic expected channel calls
    ['stopped', 'fixture-channel', true], ['stopped', 'fixture-channel', false],
    // synthetic test value — synthetic expected disabled user call
    ['storage', 'admin', 'user', 'disable', 'sink', 'fixture-user'],
    // synthetic test value — synthetic expected enabled user call
    ['storage', 'admin', 'user', 'enable', 'sink', 'fixture-user'],
  ]);
});

test('offline W17 waits past historical windows before minting follow-up work', async () => {
  const calls: unknown[] = [];
  const wait = (adapters as any).waitPastExportWindow;
  expect(typeof wait).toBe('function');
  // synthetic test value — synthetic clock
  let now = 9_500;
  // synthetic test value — synthetic persisted window timestamp
  const probe = { pg: async () => [{ latest: new Date(10_000).toISOString() }] };
  // synthetic test value — synthetic organization id and clock advancement
  await wait(probe, 'fixture-org', () => now, async (ms: number) => { calls.push(ms); now += ms; });
  // synthetic test value — synthetic elapsed-time expectation
  expect(calls).toEqual([2500]);
});

test('offline W17 S3 refusal pins the sanitized validation message', () => {
  expect(adapters.REFUSAL_TEXT.s3).toBe('Validation failed (HTTP 403) (SignatureDoesNotMatch). Check the connection settings and credentials.');
});

test('offline W17 uses the current harness-only overlay contract', () => {
  // synthetic test value — synthetic full commit fixture
  const from = 'b'.repeat(40);
  const paths = [...HARNESS_PATHS];
  expect(declaredOverlay(from, paths.map(p => `${p}/`).join(' '))).toEqual({ from, paths });
  // synthetic test value — negative product-path overlay input
  expect(() => declaredOverlay(from, 'frontend/src')).toThrow('harness paths only');
  // synthetic test value — negative empty overlay input
  expect(() => declaredOverlay(from, '')).toThrow('harness paths only');
  // synthetic test value — negative malformed commit input
  expect(() => declaredOverlay('not-a-commit', 'e2e')).toThrow('full commit id');
  for (const field of ['requestedHarness', 'product', 'composed']) {
    // synthetic test value — negative forbidden overlay field value
    expect(() => safeJson({ overlay: { from, paths, identical: true, [field]: 'c'.repeat(40) } })).toThrow();
  }
});

// W17-R1-F01 offline regression: the two panels mirror the pinned DOM structure.
// synthetic test value
const errorScopePage = (statusLabels: string[]) => `
  <section>
    <div>
      <div><div><p>Fixture destination</p><p data-testid="integration-header-destination">fixture</p></div></div>
      <div>
        ${statusLabels.map(label => `<span><span aria-hidden="true"></span><span>${label}</span></span>`).join('')}
        <button aria-label="Integration options menu"></button>
      </div>
    </div>
  </section>
  <section><p>Sync History</p><div class="ag-theme-quartz" role="grid">
    <div role="row"><div role="columnheader" col-id="error_message"><span>Error</span></div></div>
  </div></section>`;

test('offline W17 connection Error excludes the Sync History Error column header', async ({ page }) => {
  // synthetic test value
  await page.setContent(errorScopePage(['Error']));
  await expect(page.getByText('Error', { exact: true })).toHaveCount(2);
  await expect(connectionErrorStatus(page)).toHaveCount(1);
});

test('offline W17 exactly-one Error assertion rejects duplicate connection status errors', async ({ page }) => {
  // synthetic test value
  await page.setContent(errorScopePage(['Error', 'Error']));
  await expect(connectionErrorStatus(page)).toHaveCount(2);
  await expect(expect(connectionErrorStatus(page)).toHaveCount(1, { timeout: 50 }))
    .rejects.toThrow(/toHaveCount/);
});

test('offline W17 history Error alone cannot satisfy connection status', async ({ page }) => {
  // synthetic test value
  await page.setContent(errorScopePage([]));
  await expect(page.getByText('Error', { exact: true })).toHaveCount(1);
  await expect(connectionErrorStatus(page)).toHaveCount(0);
  await expect(expect(connectionErrorStatus(page)).toHaveCount(1, { timeout: 50 }))
    .rejects.toThrow(/toHaveCount/);
});
