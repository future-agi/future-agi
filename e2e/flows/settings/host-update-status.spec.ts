import { chmod, lstat, mkdir, readFile, rename, rmdir, unlink, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// frontend/src/api/hostUpdates/host-update-status.js and routes/paths.js.
const STATUS_PATH = '/api/host-update-status/';
const PAGE_PATH = '/dashboard/settings/updates';
// e2e/README.md: a browser first-paint budget, not a storage/CDC wait.
const UI_READY = 60_000;
// Frozen S01 fixtures/schema; no guessed report body or application test endpoint.
const CONTRACT = new URL('../../../api_contracts/host-update/', import.meta.url);
// HostUpdatesPage.jsx and explainer-sections.js: stable hooks and freshness copy.
const FRESHNESS = { fresh: 'Fresh', stale: 'Stale — current state unknown', unknown: 'Freshness unknown' } as const;
type Freshness = keyof typeof FRESHNESS;
type Snapshot = {
  schema_version: number; sequence: number; generated_at: string;
  installation: { slot_id: string; topology: string; production: boolean };
  versions: { running_release: string; observed_at: string; components: { component: string }[] };
  discovery: { state: string; candidate_version: string; checked_at: string; last_attempt_at: string };
};
type Envelope = { status: boolean; result: {
  availability: string; reason_code: string | null; freshness: Freshness; age_seconds: number | null;
  versions_freshness: Freshness; discovery_freshness: Freshness; snapshot: Snapshot | null;
} };
type Schema = {
  $ref?: string; $defs?: Record<string, Schema>; type?: string; additionalProperties?: boolean;
  properties?: Record<string, Schema>; items?: Schema; anyOf?: Schema[]; oneOf?: Schema[];
};

// Walk the committed closed schema, including nullable objects and array items.
// This proves the projection has no extra keys at ANY depth, not just at its root.
function assertClosedKeys(value: unknown, schema: Schema, root: Schema): void {
  if (schema.$ref) schema = root.$defs![schema.$ref.split('/').pop()!];
  const alternatives = schema.anyOf ?? schema.oneOf;
  if (alternatives) {
    if (value === null) {
      expect(alternatives.some(option => option.type === 'null')).toBe(true);
      return;
    }
    const candidates = alternatives.map(option => option.$ref
      ? root.$defs![option.$ref.split('/').pop()!] : option).filter(option => option.type !== 'null');
    // The chosen fixture has a null maintenance window, so its oneOf is not traversed.
    expect(candidates).toHaveLength(1);
    schema = candidates[0];
  }
  if (Array.isArray(value)) {
    expect(schema.type).toBe('array');
    for (const item of value) assertClosedKeys(item, schema.items!, root);
  } else if (value !== null && typeof value === 'object') {
    expect(schema.type).toBe('object');
    expect(schema.additionalProperties).toBe(false);
    const properties = schema.properties!;
    expect(Object.keys(value).filter(key => !Object.hasOwn(properties, key))).toEqual([]);
    for (const [key, item] of Object.entries(value)) assertClosedKeys(item, properties[key], root);
  }
}

test('SET-E2E-002: admin reads fresh host update reports and missing reporting stays unknown', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SET-E2E-002', area: 'settings',
    userGoal: 'An organisation admin of a self-hosted install opens Settings > Updates and sees what the host updater reported (installed version, available release, freshness), and the backend serves only a sanitized read-only projection of that report.',
    steps: [
      'atomically publish a fresh status.json from the committed valid_status fixture with a minted available version',
      'open /dashboard/settings/updates as the actor',
      'read the page fields',
      'atomically replace the report with a second minted version and reload',
      'delete the report and reload',
    ],
    backendChecks: [
      'GET /api/host-update-status/ returns 200 with the minted version and its keys are a subset of the closed public schema, with no path, env, command or secret strings',
      'the page version and freshness equal the API values',
      'an atomic replace on the host is seen by the container without restarting anything',
      'with the file removed the API reports the unavailable state and the page shows reporting unavailable and never up to date',
      'POST, PUT, PATCH and DELETE return 405 and an unauthenticated GET returns 401',
    ],
  }),
}, async ({ page, actor, request }, testInfo) => {
  test.setTimeout(240_000);
  // Three page-load stages share UI_READY within each stage: 3 × 60s + 60s for
  // seeding/API work. Five individual UI waits already exceed the 120s default.
  test.skip(process.env.E2E_STACK === 'standalone', 'Host status bind extension is Distributed-only; Standalone remains unchanged');
  const directory = process.env.E2E_HOST_UPDATE_STATUS_DIR;
  expect(directory, 'Run through bin/e2e or set the mounted E2E_HOST_UPDATE_STATUS_DIR in attach mode').toBeTruthy();
  const statusPath = join(directory!, 'status.json');
  const lockPath = join(directory!, '.set-e2e-002-lock');
  const now = Date.now();
  const suffix = `${testInfo.workerIndex}-${now.toString(36)}`;
  // Plan deviation: S01 accepts only vN.N.N[-(beta|rc).N], not -e2e-a/b-*.
  // Keep independently minted A/B anchors without weakening the public contract.
  const versionA = `v9.${testInfo.workerIndex}.${now * 2}`;
  const versionB = `v9.${testInfo.workerIndex}.${now * 2 + 1}`;
  const temporary = join(directory!, `.status-${suffix}.tmp`);
  const fixtures = JSON.parse(await readFile(new URL('fixtures.json', CONTRACT), 'utf8'));
  const snapshot: Snapshot = structuredClone(fixtures.valid_status.find(
    (item: { name: string }) => item.name === 'manual_disabled_known_current').snapshot);
  const schema: Schema = JSON.parse(await readFile(new URL('api-response.v1.schema.json', CONTRACT), 'utf8'));
  snapshot.installation.topology = 'distributed';
  snapshot.versions.components[0].component = 'backend';
  snapshot.generated_at = new Date(now).toISOString().replace(/\.\d{3}Z$/, 'Z');
  snapshot.versions.observed_at = snapshot.generated_at;
  snapshot.discovery.checked_at = snapshot.generated_at;
  snapshot.discovery.last_attempt_at = snapshot.generated_at;
  snapshot.discovery.state = 'available';
  snapshot.discovery.candidate_version = versionA;
  const headers = { Authorization: `Bearer ${actor.tokens.access}`, 'X-Organization-Id': actor.organizationId };
  await testInfo.attach('host-update-seed', { contentType: 'application/json', body: JSON.stringify({
    suffix, versionA, versionB, installedVersion: snapshot.versions.running_release,
    slotId: snapshot.installation.slot_id, organizationId: actor.organizationId, statusDirectory: directory,
    storageLane: 'Not applicable: host update reporting owns no Postgres or ClickHouse rows',
  }) });

  // Serialize only this file-owning flow; never overwrite a report from another run.
  await mkdir(lockPath);
  let ownsStatus = false;
  try {
    await expect(lstat(statusPath), 'Refuse a pre-existing host report').rejects.toMatchObject({ code: 'ENOENT' });
    await writeFile(temporary, JSON.stringify(snapshot), { mode: 0o644, flag: 'wx' });
    await chmod(temporary, 0o644);
    await rename(temporary, statusPath);
    ownsStatus = true;

    const first = await test.step('1 API: fresh sanitized projection contains the first minted candidate', async () => {
      const response = await request.get(`${E2E.apiUrl}${STATUS_PATH}`, { headers });
      expect(response.status()).toBe(200);
      const body: Envelope = await response.json();
      assertClosedKeys(body, schema, schema);
      expect(body.status).toBe(true);
      expect(body.result.availability).toBe('available');
      expect(body.result.snapshot).toEqual(snapshot);
      expect(body.result.snapshot!.discovery.candidate_version).toBe(versionA);
      expect(body.result.freshness).toBe('fresh');
      expect(body.result.versions_freshness).toBe('fresh');
      expect(body.result.discovery_freshness).toBe('fresh');
      // Fixed public scalars only: no filesystem, env, shell or secret material.
      expect(JSON.stringify(body)).not.toMatch(/\/run\/|\/Users\/|\/home\/|\.env|FI_HOST_|DOCKER_|Bearer |secret|password|token|command|docker compose|\$\(/i);
      expect(JSON.stringify(body)).not.toContain(directory!);
      return body.result;
    });

    await test.step('2 UI + API: rendered version and freshness match the actual browser response', async () => {
      const pending = page.waitForResponse(response => new URL(response.url()).pathname === STATUS_PATH &&
        response.request().method() === 'GET', { timeout: UI_READY });
      await page.goto(PAGE_PATH, { waitUntil: 'domcontentloaded' });
      const response = await pending;
      expect(response.status()).toBe(200);
      const { result }: Envelope = await response.json();
      expect(result.snapshot).toEqual(first.snapshot);
      expect([result.freshness, result.versions_freshness, result.discovery_freshness]).toEqual(['fresh', 'fresh', 'fresh']);
      await expect(page.getByTestId('host-updates-page')).toBeVisible({ timeout: UI_READY });
      await expect(page.getByTestId('host-updates-available-version')).toHaveText([versionA], { timeout: UI_READY });
      await expect(page.getByTestId('host-updates-installed-version')).toHaveText([result.snapshot!.versions.running_release], { timeout: UI_READY });
      await expect(page.getByTestId('host-updates-version-freshness')).toHaveText([FRESHNESS[result.versions_freshness]], { timeout: UI_READY });
      await expect(page.getByTestId('host-updates-check-freshness')).toHaveText([FRESHNESS[result.discovery_freshness]], { timeout: UI_READY });
      const summary = page.getByTestId('host-updates-summary');
      await expect(summary.getByText('Latest host report', { exact: true })).toHaveText(['Latest host report'], { timeout: UI_READY });
      await expect(summary.locator('time')).toHaveAttribute('datetime', result.snapshot!.generated_at, { timeout: UI_READY });
      await expect(summary.getByText(/^As of /)).toHaveText([
        `As of ${result.snapshot!.generated_at.replace('T', ' ').replace('Z', ' UTC')}. Report age when read: ${result.age_seconds} seconds.`,
      ], { timeout: UI_READY });
    }, { timeout: UI_READY });

    await test.step('3 UI + API: atomic host replacement reaches the same running stack', async () => {
      const previous = await lstat(statusPath);
      snapshot.sequence += 1;
      snapshot.generated_at = new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
      snapshot.discovery.checked_at = snapshot.generated_at;
      snapshot.discovery.last_attempt_at = snapshot.generated_at;
      snapshot.discovery.candidate_version = versionB;
      await writeFile(temporary, JSON.stringify(snapshot), { mode: 0o644, flag: 'wx' });
      await chmod(temporary, 0o644);
      await rename(temporary, statusPath);
      expect((await lstat(statusPath)).ino).not.toBe(previous.ino);
      const response = await request.get(`${E2E.apiUrl}${STATUS_PATH}`, { headers });
      expect(response.status()).toBe(200);
      const { result }: Envelope = await response.json();
      expect(result.snapshot).toEqual(snapshot);
      expect(result.snapshot!.discovery.candidate_version).toBe(versionB);
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect(page.getByTestId('host-updates-available-version')).toHaveText([versionB], { timeout: UI_READY });
      await expect(page.getByTestId('host-updates-installed-version')).toHaveText([snapshot.versions.running_release], { timeout: UI_READY });
    }, { timeout: UI_READY });

    await test.step('4 UI + API: missing file is unavailable, never up to date', async () => {
      await unlink(statusPath);
      ownsStatus = false;
      const response = await request.get(`${E2E.apiUrl}${STATUS_PATH}`, { headers });
      expect(response.status()).toBe(200);
      const body: Envelope = await response.json();
      assertClosedKeys(body, schema, schema);
      expect(body.result).toEqual({ api_schema_version: 1, availability: 'unavailable', reason_code: 'not_reported',
        freshness: 'unknown', age_seconds: null, discovery_freshness: 'unknown', versions_freshness: 'unknown',
        progress_freshness: 'unknown', snapshot: null });
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect(page.getByTestId('host-updates-unavailable')).toHaveText(['Reporting unavailable'], { timeout: UI_READY });
      await expect(page.getByTestId('host-updates-available-version')).toHaveCount(0, { timeout: UI_READY });
      await expect(page.getByTestId('host-updates-installed-version')).toHaveCount(0, { timeout: UI_READY });
      await expect(page.getByText(/^(up.to.date|current at last verified check)$/i)).toHaveCount(0, { timeout: UI_READY });
    }, { timeout: UI_READY });

    await test.step('5 API: mutations are 405 and anonymous access is 401', async () => {
      for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) {
        const response = await request.fetch(`${E2E.apiUrl}${STATUS_PATH}`, { method, headers, data: {} });
        expect(response.status(), method).toBe(405);
      }
      const anonymous = await request.get(`${E2E.apiUrl}${STATUS_PATH}`);
      expect(anonymous.status()).toBe(401);
    });
  } finally {
    // Only files created by this flow, never a recursive directory/global reset.
    if (ownsStatus) await unlink(statusPath);
    await unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error; });
    await rmdir(lockPath);
  }
});
