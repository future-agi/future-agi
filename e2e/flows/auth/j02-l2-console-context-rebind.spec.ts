// J02.L2 source-only draft at 48f3e661e293e5fc250c3d29b770e9618491b57d.
// DRAFT_UNREVIEWED / NOT_RUN. No adapter, fixture, or execution grant is supplied.
// SOURCE-MAP-r1.json pins every host selector/route; EE selectors are UNKNOWN.
// Not a catalog flow (no @flow tag): playwright.config.ts testIgnore keeps it out of
// the ordinary suite until a dedicated config binds an admitted adapter.
import { test, expect } from '@playwright/test';
import type { Page, TestInfo } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { isAbsolute } from 'node:path';
import { pathToFileURL } from 'node:url';

type Witness = Record<string, boolean>;
type Workspace = { id: string; label: string };
type Lease = {
  appOrigin: string; apiOrigin: string; a1: Workspace; a2: Workspace;
  foreign: Workspace; inactive: Workspace; setupVerified: boolean;
};
type Adapter = {
  prepare: (arg: { caseId: string; page: Page }) => Promise<Lease>;
  observe: (arg: { caseId: string; checkpoint: string; workspace: string }) => Promise<Witness>;
  operate: (arg: { caseId: string; phase: string; workspace: string }) => Promise<Witness>;
  refuseSwitch: (arg: { caseId: string; workspace: string }) => Promise<{ status: number; noEffect: boolean }>;
  control: (arg: { caseId: string; kind: string; from: string; to: string }) => Promise<Witness>;
  cleanup: (arg: { caseId: string }) => Promise<Witness>;
};
const SWITCH = '/accounts/workspace/switch/'; // API_SWITCH + API_MOUNT
const DEVELOP = '/dashboard/develop'; // WORKSPACE_SWITCH
const CONSOLE = '/dashboard/console'; // CONSOLE_ROUTE + CONSOLE_MOUNT
const TIMEOUT = 60_000; // Proposed action bound, not an observed venue latency.
const CASES = [
  ['J02.L2-001', 'A1 to A2 to A1 Console context round trip', 'roundtrip'],
  ['J02.L2-002', 'foreign tenant workspace refusal preserves selected context', 'foreign'],
  ['J02.L2-003', 'owner global workspace access and Console re-entry', 'owner'],
  ['J02.L2-004', 'admin global workspace access and Console re-entry', 'admin'],
  ['J02.L2-005', 'active member workspace access and Console re-entry', 'member'],
  ['J02.L2-006', 'viewer Console refusal is not exchange readiness', 'viewer'],
  ['J02.L2-007', 'revoked non-global membership refuses the target workspace', 'revoked'],
  ['J02.L2-008', 'stale tab cannot misattribute old context to the new workspace', 'stale-tab'],
  ['J02.L2-009', 'stale document ready cannot connect the replacement document', 'stale-document'],
  ['J02.L2-010', 'late exchange response cannot populate the replacement document', 'inflight'],
  ['J02.L2-011', 'selected context persists through reload and Console re-entry', 'persistence'],
  ['J02.L2-012', 'old document operation is rejected while new context is ready', 'old-context'],
  ['J02.L2-013', 'inactive workspace refusal preserves selected context', 'inactive'],
] as const;

// Deliberately no fake implementation or guessed server/tool. A later producer
// successor supplies this names-only metadata after its real execution admission.
// Merely filling this metadata does NOT grant authority to run.
async function loadAdapter(info: TestInfo): Promise<Adapter> {
  const b = info.project.metadata['J02.L2'];
  if (!b || b.status !== 'BOUND_FOR_ADMITTED_RUN' || b.child !== 'J02.L2'
      || typeof b.adapterPath !== 'string' || !isAbsolute(b.adapterPath)
      || !b.adapterPath.endsWith('.mjs') || !/^[a-f0-9]{64}$/.test(b.adapterSha256 || '')
      || b.producerSigned !== true || b.collectorBound !== true || b.finalTupleBound !== true) {
    throw new Error('J02.L2 UNKNOWN_ADAPTER_OR_ADMISSION_BINDINGS');
  }
  const bytes = await readFile(b.adapterPath);
  if (createHash('sha256').update(bytes).digest('hex') !== b.adapterSha256) {
    throw new Error('J02.L2 ADAPTER_DIGEST_MISMATCH');
  }
  const module = await import(pathToFileURL(b.adapterPath).href);
  if (typeof module.createJ02L2Adapter !== 'function') throw new Error('J02.L2 ADAPTER_FACTORY_MISSING');
  const adapter = await module.createJ02L2Adapter({ binding: b });
  for (const name of ['prepare', 'observe', 'operate', 'refuseSwitch', 'control', 'cleanup']) {
    if (typeof adapter?.[name] !== 'function') throw new Error('J02.L2 ADAPTER_METHOD_MISSING');
  }
  return adapter;
}

function requireWitness(actual: Witness, names: string[]) {
  // Never stringify untrusted adapter fields or assertions. Only booleans leave.
  expect(actual !== null && typeof actual === 'object', 'names-only witness required').toBe(true);
  expect(Object.keys(actual).every(k => names.includes(k)), 'unexpected witness field').toBe(true);
  for (const name of names) expect(actual[name] === true, `missing or false witness: ${name}`).toBe(true);
}
const CONTEXT = ['selectedContextMatches', 'persistedPreferencesMatch', 'sameInteractiveSession', 'scopeMatches', 'originalWindow'];
const EFFECT = ['newContextReady', 'operationCompleted', 'effectMatchesSelectedScope', 'foreignEffectAbsent', 'independentWitnessAvailable', 'originalWindow'];
const CONTROL = ['positiveObserved', 'refusalObserved', 'nonEffectObserved', 'restoredObserved', 'finalObserved', 'originalWindow', 'independentWitnessAvailable'];

async function context(adapter: Adapter, caseId: string, workspace: string, checkpoint: string) {
  requireWitness(await adapter.observe({ caseId, checkpoint, workspace }), CONTEXT);
}
async function effect(adapter: Adapter, caseId: string, workspace: string, phase: string) {
  // A host connected state or a successful session-exchange response is NOT enough.
  requireWitness(await adapter.operate({ caseId, phase, workspace }), EFFECT);
}
async function reenter(page: Page, lease: Lease) {
  await page.goto(new URL(CONSOLE, lease.appOrigin).href, { waitUntil: 'domcontentloaded' });
  await expect(page.getByTestId('omega-tab-console')).toBeVisible(); // FRAME_STATE
  // The real EE consumer/effect oracle is required separately, never mocked here.
}
async function switchViaUI(page: Page, lease: Lease, from: Workspace, to: Workspace) {
  // Console is full-width without the FA workspace switcher (CONSOLE_ROUTE).
  // Return to Develop, use the real FA switcher, then explicitly re-enter Console.
  await page.goto(new URL(DEVELOP, lease.appOrigin).href, { waitUntil: 'domcontentloaded' });
  const timeOrigin = await page.evaluate(() => performance.timeOrigin);
  const current = page.getByText(from.label, { exact: true }).filter({ visible: true });
  await expect(current).toHaveCount(1);
  await current.click();
  await page.locator('.MuiPopover-root:visible').getByText(from.label, { exact: true }).hover();
  const [response] = await Promise.all([
    page.waitForResponse(r => new URL(r.url()).origin === new URL(lease.apiOrigin).origin
      && new URL(r.url()).pathname === SWITCH && r.request().method() === 'POST'),
    page.getByRole('tooltip').getByText(to.label, { exact: true }).click(),
  ]);
  expect(response.status()).toBe(200);
  const body = response.request().postDataJSON();
  expect(body?.old_workspace_id === from.id && body?.new_workspace_id === to.id,
    'real workspace switch POST must bind both synthetic workspaces').toBe(true);
  await page.waitForURL(new URL(DEVELOP, lease.appOrigin).href, { waitUntil: 'domcontentloaded' });
  // URL alone may already equal Develop before the click. Require a NEW document.
  await expect.poll(async () => page.evaluate(() => performance.timeOrigin)).not.toBe(timeOrigin);
  await reenter(page, lease);
}

for (const [caseId, title, kind] of CASES) {
  test(`${caseId}: ${title}`, { tag: ['@j02-l2'] }, async ({ page }, info) => {
    test.setTimeout(540_000);
    page.setDefaultTimeout(TIMEOUT);
    page.setDefaultNavigationTimeout(TIMEOUT);
    let adapter: Adapter | undefined;
    let failed = false;
    try {
      adapter = await loadAdapter(info); // fail closed; no test.skip and no default adapter
      const lease = await adapter.prepare({ caseId, page });
      expect(lease.setupVerified === true, 'synthetic fixture must be verified before actions').toBe(true);
      expect(lease.a1.id !== lease.a2.id && lease.a1.label !== lease.a2.label, 'distinct A1/A2 fixture').toBe(true);
      await reenter(page, lease);
      await context(adapter, caseId, lease.a1.id, 'baseline');
      if (kind === 'foreign' || kind === 'inactive' || kind === 'revoked') {
        // Adapter issues the SAME authenticated product POST, not a fake UI option.
        // The target is not selectable in the UI for these fixtures.
        const target = kind === 'foreign' ? lease.foreign.id : kind === 'inactive' ? lease.inactive.id : lease.a2.id;
        const refusal = await adapter.refuseSwitch({ caseId, workspace: target });
        expect(refusal.status).toBe(kind === 'revoked' ? 403 : 404); // SWITCH_AUTHZ + response helpers
        expect(refusal.noEffect === true).toBe(true);
        await context(adapter, caseId, lease.a1.id, 'after-refusal');
        requireWitness(await adapter.control({ caseId, kind, from: lease.a1.id, to: target }), CONTROL);
      } else if (kind === 'viewer') {
        await switchViaUI(page, lease, lease.a1, lease.a2);
        await context(adapter, caseId, lease.a2.id, 'viewer-selection');
        await expect(page.getByTestId('omega-tab-console')).toHaveAttribute('data-state', 'viewer');
        // Actual EE refusal/non-effect and restored non-viewer control are still mandatory.
        requireWitness(await adapter.control({ caseId, kind, from: lease.a1.id, to: lease.a2.id }), CONTROL);
      } else {
        await effect(adapter, caseId, lease.a1.id, 'positive-A1');
        if (['stale-tab', 'stale-document', 'inflight', 'old-context'].includes(kind)) {
          // Producer control owns the REAL race schedule, including trigger of the
          // same switch flow. No page.route(), fulfilled response or forged ready.
          requireWitness(await adapter.control({ caseId, kind, from: lease.a1.id, to: lease.a2.id }), CONTROL);
          await reenter(page, lease);
        } else {
          await switchViaUI(page, lease, lease.a1, lease.a2);
        }
        await context(adapter, caseId, lease.a2.id, 'selected-A2');
        await effect(adapter, caseId, lease.a2.id, 'new-A2');
        if (kind === 'persistence') {
          await page.reload({ waitUntil: 'domcontentloaded' });
          await context(adapter, caseId, lease.a2.id, 'reload-A2');
          await reenter(page, lease);
          await effect(adapter, caseId, lease.a2.id, 'reentry-A2');
        }
        await switchViaUI(page, lease, lease.a2, lease.a1);
        await context(adapter, caseId, lease.a1.id, 'restored-A1');
        await effect(adapter, caseId, lease.a1.id, 'restored-A1');
        // Check old-document rejection, not blanket invalidation of all otherwise
        // authorized A1 credentials. No product promise of global revocation here.
        requireWitness(await adapter.control({ caseId, kind: 'final-old-document-refusal', from: lease.a2.id, to: lease.a1.id }), CONTROL);
      }
      await info.attach(`${caseId}-producer-checks`, {
        contentType: 'application/json',
        body: JSON.stringify({ caseId, producerChecksCompleted: true, independentReceiptAccepted: false }),
      });
    } catch {
      failed = true; // Suppress arbitrary adapter/runtime errors that could carry values.
    } finally {
      if (adapter) {
        try {
          // prepare MUST rollback partial allocations too; cleanup is idempotent
          // and scoped only to this case's synthetic lease, never a shared sweep.
          requireWitness(await adapter.cleanup({ caseId }), ['ownedFixturesRemoved', 'contextRestored', 'finalNonEffectObserved']);
        } catch { failed = true; }
      }
    }
    expect(failed, 'J02.L2 failed or has UNKNOWN bindings; retain names-only failure receipt').toBe(false);
  });
}
