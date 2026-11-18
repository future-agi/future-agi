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
  versions: { running_release: string | null; runtime_state: string; observed_at: string; components: { component: string }[] };
  discovery: { state: string; candidate_version: string; checked_at: string; last_attempt_at: string };
  operation: { state: string; kind: string | null; stage: string | null; phase?: string | null; reported_at: string | null };
  safety: { backup_state: string; fallback_strategy?: string; restore_state?: string; snapshot_at?: string;
    loss_window_start?: string; loss_window_end?: string; loss_window_qualification?: string;
    guidance_id?: string; estimates?: null; failed_target_version?: string };
  last_run: { kind: string; automatic: boolean; outcome: string; from_version: string; target_version: string;
    finished_at: string; fallback_outcome: string; forward_result: string; restore_result: string;
    effective_version: string | null; failed_target_version: string } | null;
};
type Envelope = { status: boolean; result: {
  availability: string; reason_code: string | null; freshness: Freshness; age_seconds: number | null;
  versions_freshness: Freshness; discovery_freshness: Freshness; snapshot: Snapshot | null;
} };
type Schema = {
  $ref?: string; $defs?: Record<string, Schema>; type?: string; additionalProperties?: boolean;
  properties?: Record<string, Schema>; items?: Schema; anyOf?: Schema[]; oneOf?: Schema[];
  required?: string[]; maxProperties?: number; const?: unknown;
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
      ? root.$defs![option.$ref.split('/').pop()!] : option).filter(option => {
        if (option.type === 'null') return false;
        if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
          const object = value as Record<string, unknown>;
          if (option.required?.some(key => !Object.hasOwn(object, key))) return false;
          if (option.maxProperties !== undefined && Object.keys(object).length > option.maxProperties) return false;
          return Object.entries(option.properties ?? {}).every(([key, property]) =>
            !Object.hasOwn(property, 'const') || object[key] === property.const);
        }
        return true;
      });
    // Legacy and complete recovery groups are disjoint: select by the actual
    // required keys/count, never assume anyOf means one object plus null.
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
    userGoal: 'An organisation admin reads host-reported restoration progress and the restored previous version separately from a failed update, alongside available release and freshness, through a sanitized read-only report; missing reporting stays unknown.',
    steps: [
      'atomically publish a fresh status.json from the committed valid_status fixture with a minted available version',
      'open /dashboard/settings/updates as the actor',
      'read restoring progress with an unverified installed version and recovery explanation',
      'atomically replace the report with restored status, a known running component and a second minted candidate, then reload',
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
  const installedVersion = snapshot.versions.running_release!;
  // status.py:_semantics requires a known active component before consistency
  // can be reported. Save it for the restored report, never as live evidence
  // during recovery. The distributed wire fixture reports the backend component.
  const restoredComponents = snapshot.versions.components.map(item => ({
    ...item, component: item.component === 'standalone' ? 'backend' : item.component,
  }));
  // Wire-reader fixture only, not a real updater/restoration qualification.
  // Additions pinned from tfc/host_updates/status.py and the committed schema.
  Object.assign(snapshot.operation, { state: 'restoring', kind: 'restore', stage: 'restore', phase: 'restoring_volumes' });
  Object.assign(snapshot.safety, {
    backup_state: 'sealed', fallback_strategy: 'restore_backup', restore_state: 'running',
    loss_window_qualification: 'bounded', guidance_id: 'none', estimates: null, failed_target_version: versionA,
  });
  snapshot.last_run = {
    kind: 'apply', automatic: true, outcome: 'failed_during_rollout', from_version: installedVersion,
    target_version: versionA, finished_at: '', fallback_outcome: 'none', forward_result: 'failed',
    restore_result: 'running', effective_version: null, failed_target_version: versionA,
  };
  Object.assign(snapshot.versions, { running_release: null, runtime_state: 'unknown', components: [] });
  snapshot.installation.topology = 'distributed';
  snapshot.generated_at = new Date(now).toISOString().replace(/\.\d{3}Z$/, 'Z');
  snapshot.versions.observed_at = snapshot.generated_at;
  snapshot.operation.reported_at = snapshot.generated_at;
  snapshot.last_run.finished_at = snapshot.generated_at;
  snapshot.safety.snapshot_at = snapshot.generated_at;
  snapshot.safety.loss_window_start = snapshot.generated_at;
  snapshot.safety.loss_window_end = snapshot.generated_at;
  snapshot.discovery.checked_at = snapshot.generated_at;
  snapshot.discovery.last_attempt_at = snapshot.generated_at;
  snapshot.discovery.state = 'available';
  snapshot.discovery.candidate_version = versionA;
  const headers = { Authorization: `Bearer ${actor.tokens.access}`, 'X-Organization-Id': actor.organizationId };
  await testInfo.attach('host-update-seed', { contentType: 'application/json', body: JSON.stringify({
    suffix, versionA, versionB, installedVersion,
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
      await expect(page.getByTestId('host-updates-installed-version')).toHaveText(['Unknown'], { timeout: UI_READY });
      await expect(page.getByText('Restoring the cold backup and previous version', { exact: true })).toHaveText(['Restoring the cold backup and previous version'], { timeout: UI_READY });
      await page.getByRole('button', { name: 'Automatic restoration is not image-only rollback' }).click();
      await expect(page.getByText(/a failure after the cold backup restores that backup and the previous version automatically/)).toBeVisible({ timeout: UI_READY });
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
      Object.assign(snapshot.operation, { state: 'restored', phase: 'restored', reported_at: snapshot.generated_at });
      Object.assign(snapshot.safety, { restore_state: 'verified' });
      Object.assign(snapshot.versions, { components: restoredComponents, running_release: installedVersion, runtime_state: 'consistent', observed_at: snapshot.generated_at });
      Object.assign(snapshot.last_run!, { restore_result: 'verified', effective_version: installedVersion, finished_at: snapshot.generated_at });
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
      await expect(page.getByTestId('host-updates-installed-version')).toHaveText([installedVersion], { timeout: UI_READY });
      await expect(page.getByText('Cold backup and previous version restored', { exact: true })).toHaveText(['Cold backup and previous version restored', 'Cold backup and previous version restored'], { timeout: UI_READY });
      await expect(page.getByTestId('host-updates-summary').getByText(`Update to ${versionA} failed; restored ${installedVersion}; ${versionA} will not be retried automatically. Restore discards new-version writes from ${snapshot.safety.loss_window_start} to ${snapshot.safety.loss_window_end} (bounded window). External service calls cannot be undone.`, { exact: true })).toBeVisible({ timeout: UI_READY });
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
        const response = await request.fetch(`${E2E.apiUrl}${STATUS_PATH}`, { method, headers });
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
