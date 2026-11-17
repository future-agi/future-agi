// W17 destination adapters. Wire/form contracts are pinned to ec94c6dba4:
// integrations/services/{datadog,mixpanel,posthog,cloud_storage}_service.py,
// services/export_destinations.py and the INT-E2E-002/003/004/005 base flows.
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { expect, type Page } from '@playwright/test';
import { EXPORT_CHANNELS, EXPORT_ENDPOINTS, posthogHost, refusedCredential, StorageSink, type StorageUser } from '../export-sinks';
import { WebhookSink, mintScope, type ReceivedWebhook } from '../sinks';
import { browserLocalTime, connectExport, openPlatform, validateCredentials } from '../integration-export';
import { inspectManagedMock, containerEnvironment, type Container } from '../managed-mock';
import { assertValueFreeReceipt } from './receipt-values.mjs';

export const UI_READY = 60_000;
// Vendor validation sentences pinned by INT-E2E-002/003/004/005.
export const REFUSAL_TEXT = {
  datadog: 'Invalid API key. Please check your Datadog API key.',
  mixpanel: 'Invalid project token. Please check your Mixpanel project token.',
  posthog: 'Invalid API key. Please check your PostHog project API key.',
  // cloud_storage_service.py delegates ClientError to export_failures.validation_failure.
  s3: 'Validation failed (HTTP 403) (SignatureDoesNotMatch). Check the connection settings and credentials.',
};
const root = fileURLToPath(new URL('../../../', import.meta.url));
export const fingerprint = (value: unknown) => `sha256:${createHash('sha256').update(JSON.stringify(value)).digest('hex')}`;
export const safeJson = (value: unknown) => {
  const json = JSON.stringify(value); assertValueFreeReceipt(json); return json;
};

export function assertStorageMounts(mounts: Container['Mounts']) {
  expect(mounts.every(m => m.Type === 'volume' && m.Destination === '/data' && m.RW)).toBe(true);
}

/** Extend, never replace, H1's routing/source inspection. Refuse missing or foreign
 * operator overrides before a browser can validate credentials at a vendor. */
export function inspectExportStack() {
  expect(process.env.E2E_STACK ?? 'distributed').toBe('distributed');
  const receipt = inspectManagedMock({ exportSinks: true });
  const docker = (...args: string[]) => execFileSync('docker', ['--context', receipt.context, ...args],
    { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], timeout: 30_000 });
  const ids = execFileSync(`${root}bin/e2e`, ['compose', 'ps', '-q', 'backend', 'worker', 'storage-sink'],
    { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], timeout: 30_000 }).trim().split(/\s+/);
  expect(ids).toHaveLength(3);
  const containers = JSON.parse(docker('inspect', ...ids)) as Container[];
  for (const service of ['backend', 'worker']) {
    const c = containers.filter(c => c.Config.Labels['com.docker.compose.service'] === service);
    expect(c).toHaveLength(1);
    const env = containerEnvironment(c[0]);
    expect(Object.fromEntries(Object.entries(env).filter(([name]) => name.startsWith('INTEGRATIONS_'))))
      .toEqual(EXPORT_ENDPOINTS);
  }
  const storage = containers.filter(c => c.Config.Labels['com.docker.compose.service'] === 'storage-sink');
  expect(storage).toHaveLength(1);
  expect(storage[0].State.Running).toBe(true);
  expect(storage[0].Config.Labels['com.docker.compose.project']).toBe(receipt.project);
  expect(Object.values(storage[0].NetworkSettings.Networks).map(n => n.NetworkID)).toEqual([receipt.networkId]);
  expect(Object.values(storage[0].NetworkSettings.Ports).filter(Boolean)).toEqual([]);
  expect(storage[0].HostConfig.ExtraHosts ?? []).toEqual([]);
  // MinIO may declare an anonymous /data volume in its image. Permit only
  // that data volume, never a host bind or an application/source mount.
  assertStorageMounts(storage[0].Mounts);
  return { ...receipt, storageImage: storage[0].Image, exportConfig: fingerprint(EXPORT_ENDPOINTS) };
}

export interface Destination {
  kind: 'datadog' | 'mixpanel' | 'posthog' | 's3';
  platform: string; name: string; host: string; destination: string;
  credential: string; refused: string; fields: Record<string, string>; badFields: Record<string, string>;
  channel?: string; prefix?: string; user?: StorageUser; id?: string;
}

export function destinations(suffix: string, storage: StorageSink): Destination[] {
  // synthetic test value — synthetic isolated PostHog channel
  const channel = mintScope('w17-posthog');
  // synthetic test value — synthetic storage prefix
  const prefix = `w17-${suffix}`;
  // synthetic test value — synthetic isolated storage user
  const user = storage.addUser(`w17${suffix}`.replace(/-/g, '').slice(-20), prefix);
  return [
    // synthetic test value — synthetic destination configuration; host is a pinned product contract routed to the attested sink
    { kind: 'datadog', platform: 'Datadog', name: 'Datadog', host: 'https://api.datadoghq.eu',
      // synthetic test value — synthetic destination selection; host is a pinned product contract
      destination: 'https://api.datadoghq.eu', channel: EXPORT_CHANNELS.datadog,
      // synthetic test value — synthetic positive/refused Datadog credentials
      credential: `e2e-dd-${suffix}`, refused: refusedCredential(`dd-${suffix}`),
      // synthetic test value — synthetic Datadog form input
      fields: { 'API Key': `e2e-dd-${suffix}` }, badFields: { 'API Key': refusedCredential(`dd-${suffix}`) } },
    // synthetic test value — synthetic destination configuration; host is a pinned product contract routed to the attested sink
    { kind: 'mixpanel', platform: 'Mixpanel', name: 'Mixpanel', host: 'https://api.mixpanel.com',
      // synthetic test value — synthetic destination selection; host is a pinned product contract
      destination: 'https://api.mixpanel.com', channel: EXPORT_CHANNELS.mixpanel,
      // synthetic test value — synthetic positive/refused Mixpanel credentials
      credential: `e2e-mp-${suffix}`, refused: refusedCredential(`mp-${suffix}`),
      // synthetic test value — synthetic Mixpanel form input
      fields: { 'Project Token': `e2e-mp-${suffix}` }, badFields: { 'Project Token': refusedCredential(`mp-${suffix}`) } },
    // synthetic test value — synthetic PostHog destination
    { kind: 'posthog', platform: 'PostHog', name: 'PostHog', host: posthogHost(channel),
      // synthetic test value — synthetic scoped host/channel
      destination: posthogHost(channel), channel,
      // synthetic test value — synthetic positive/refused PostHog credentials
      credential: `e2e-ph-${suffix}`, refused: refusedCredential(`ph-${suffix}`),
      // synthetic test value — synthetic PostHog form input
      fields: { 'Project API Key': `e2e-ph-${suffix}` }, badFields: { 'Project API Key': refusedCredential(`ph-${suffix}`) } },
    // synthetic test value — synthetic isolated S3 host
    { kind: 's3', platform: 'Cloud Storage', name: 'Amazon S3', host: `https://${storage.bucket}.s3.${storage.region}.amazonaws.com/${prefix}`,
      // synthetic test value — synthetic S3 destination/prefix/user
      destination: `s3://${storage.bucket}/${prefix} in ${storage.region}`, prefix, user,
      // synthetic test value — synthetic isolated storage credential and invalid derivative
      credential: user.secretAccessKey, refused: `${user.secretAccessKey}-wrong`,
      // synthetic test value — synthetic storage form inputs
      fields: { 'Access Key ID': user.accessKeyId, 'Secret Access Key': user.secretAccessKey },
      // synthetic test value — negative storage credential input
      badFields: { 'Secret Access Key': `${user.secretAccessKey}-wrong` } },
  ];
}

/** Reversible input failures, isolated by this walk's credential/channel/user.
 * MinIO's admin user disable/enable preserves the exact credential and policy;
 * never stop the shared storage container or disturb the other browser. */
export async function setDestinationFailure(d: Destination, sink: WebhookSink, down: boolean,
  runStorage: (args: string[]) => unknown = args => execFileSync(`${root}bin/e2e`,
    ['compose', 'exec', '-T', 'storage-sink', 'mc', ...args],
    { timeout: 60_000, stdio: ['ignore', 'pipe', 'pipe'] })) {
  if (d.kind === 's3') {
    if (!d.user) throw new Error('W17 storage user is required');
    runStorage(['admin', 'user', down ? 'disable' : 'enable', 'sink', d.user.accessKeyId]);
  } else if (d.kind === 'posthog') {
    await sink.stopped(d.channel!, down);
  } else {
    await sink.revoked(d.credential, down);
  }
}

/** mintRequestLogs(count=1) backdates by one second. Wait before minting so a
 * newly inserted input cannot retroactively enter a sync's already-read set. */
export async function waitPastExportWindow(probe: import('../state-probe').StateProbe, org: string,
  now = Date.now, pause: (ms: number) => Promise<void> = ms => new Promise(resolve => setTimeout(resolve, ms))) {
  const [row] = await probe.pg<{ latest: string | null }>(`SELECT max(s.sync_to)::text AS latest
    FROM integrations_sync_log s JOIN integrations_connection c ON c.id = s.connection_id
    WHERE c.organization_id = $1 AND s.completed_at IS NOT NULL`, [org]);
  const delay = row?.latest ? Date.parse(row.latest) + 2000 - now() : 0;
  if (delay > UI_READY) throw new Error('export window is ahead of the runner clock');
  if (delay > 0) await pause(delay);
}

export async function fillCredentials(page: Page, d: Destination, storage: StorageSink) {
  await openPlatform(page, d.platform, UI_READY);
  if (d.kind === 'datadog') {
    await page.getByLabel('Datadog Site').click();
    await page.getByRole('option', { name: 'EU1 (datadoghq.eu)' }).click();
  }
  if (d.kind === 'posthog') {
    await page.getByRole('button', { name: 'Using self-hosted PostHog?' }).click();
    await page.getByLabel('Host URL').fill(d.host);
  }
  if (d.kind === 's3') {
    await page.getByLabel('Bucket Name').fill(storage.bucket);
    await page.getByLabel('Key Prefix (Optional)').fill(d.prefix!);
  }
  for (const [label, value] of Object.entries(d.fields)) await page.getByLabel(label).fill(value, { timeout: UI_READY });
}

export async function connectDestination(page: Page, d: Destination) {
  for (const [label, value] of Object.entries(d.fields)) await page.getByLabel(label).fill(value, { timeout: UI_READY });
  expect(await validateCredentials(page, UI_READY)).toBe(200);
  // All destinations explicitly start at 18 h: the 20 h decoy must never export.
  // Only Datadog defaults to 18 h; the others default to 24 h in the product.
  // synthetic test value — synthetic 18-hour window input
  const start = await browserLocalTime(page, 18);
  // synthetic test value — synthetic interval selection
  return connectExport(page, UI_READY, { interval: 'Every 30 minutes',
    // synthetic test value — synthetic start-window selection
    recentHours: d.kind === 'datadog' ? 18 : 24, fromDate: d.kind === 'datadog' ? undefined : start.value,
    sends: { host_url: d.host, display_name: d.name } });
}

type Event = { event: string; properties: Record<string, unknown> };
export async function deliveries(d: Destination, sink: WebhookSink, storage: StorageSink): Promise<string[]> {
  if (d.kind === 's3') return storage.objects(d.prefix!).flatMap(o => o.records.map(r => String(r.request_id))).sort();
  const calls = await sink.received(d.channel!);
  if (d.kind === 'datadog') return calls.filter(c => c.headers['dd-api-key'] === d.credential && c.status === 202 &&
    // synthetic test value — synthetic URL parsing base, not a network call
    new URL(c.path, 'http://sink').pathname.endsWith('/api/v2/logs'))
    .flatMap(c => (c.body as { attributes: Record<string, unknown> }[]).map(e => String(e.attributes.request_id))).sort();
  if (d.kind === 'mixpanel') return calls.filter(c => c.status === 200 && c.accepted === true && Array.isArray(c.body))
    .flatMap(c => (c.body as Event[]).filter(e => e.properties.token === d.credential && e.event === 'agentcc_request')
      .map(e => String(e.properties.request_id))).sort();
  return calls.filter(c => c.status === 200 && (c.body as { api_key?: string })?.api_key === d.credential &&
    // synthetic test value — synthetic URL parsing base, not a network call
    new URL(c.path, 'http://sink').pathname.endsWith('/batch/'))
    .flatMap(c => (c.body as { batch: Event[] }).batch.map(e => String(e.properties.request_id))).sort();
}

export async function ownCalls(d: Destination, sink: WebhookSink): Promise<ReceivedWebhook[]> {
  const calls = await sink.received(d.channel!);
  if (d.kind === 'datadog') return calls.filter(c => c.headers['dd-api-key'] === d.credential);
  return calls;
}

/** Inspect in memory only. The returned receipt has no log, credential or ID. */
export function assertNoCredentials(values: unknown, credentials: string[]) {
  const text = JSON.stringify(values);
  expect(credentials.some(c => text.includes(c)), 'credential disclosed in diagnostics').toBe(false);
}
export function assertAppLogsClean(credentials: string[]) {
  const logs = execFileSync(`${root}bin/e2e`, ['logs', '--no-color', 'backend', 'worker'],
    { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], timeout: 30_000, maxBuffer: 64 * 1024 * 1024 });
  assertNoCredentials(logs, credentials);
  return fingerprint(logs);
}
