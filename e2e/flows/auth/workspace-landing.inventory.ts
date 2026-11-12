// Receipt reconciliation only, NOT a browser runner or evidence acceptance gate.
// Stage A supplies these ten explicit paths after both unchanged browser jobs.
import { strict as assert } from 'node:assert';
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { TestInfo } from '@playwright/test';
import type { ScopeActors } from '../../lib/scope-actors';
import type { CaseRow } from './workspace-landing.browser';

const CELLS = ['landing', 'invite-link', 'invite-deadline-expired-pending', 'invite-cancelled', 'invite-missing-authority'] as const;
const NON_MAIL = ['reversed-uuid', 'membership-order', 'is-default', 'stale-preference', 'cross-org-preference',
  'saml-scoped-default', 'owner-without-membership', 'member-without-membership',
  'saved-preference', 'legacy-current', 'configured-default', 'inactive-preference'];
const landingTitle = 'AUTH-E2E-005: workspace landing stays within the current organization and authorized workspaces';
type Cell = typeof CELLS[number];
interface Inventory {
  schema: 'auth004-inventory/v1'; cell: Cell; title: string; browser: string; project: string;
  retry: number; repeat: number; tenantDigests: string[]; rows: CaseRow[];
}
export function recordLandingInventory(info: TestInfo, browser: string, cell: Cell, scopes: ScopeActors, rows: CaseRow[]) {
  const data: Inventory = { schema: 'auth004-inventory/v1', cell, title: info.title, browser, project: info.project.name,
    retry: info.retry, repeat: info.repeatEachIndex,
    tenantDigests: [scopes.ownerA.organizationId, scopes.ownerB.organizationId]
      .map(id => createHash('sha256').update(id).digest('hex')), rows };
  writeFileSync(info.outputPath(`landing-inventory-${cell}.json`), JSON.stringify(data));
}
export function reconcileLandingInventory(records: Inventory[]) {
  assert.equal(records.length, 10, 'J02.L1.A13 requires all five tests in both browsers');
  const tenants = new Set<string>();
  for (const browser of ['chromium', 'firefox']) for (const cell of CELLS) {
    const matches = records.filter(r => r.browser === browser && r.cell === cell);
    assert.equal(matches.length, 1, 'J02.L1.A13 missing or duplicate browser/test cell');
    const r = matches[0];
    assert.equal(r.schema, 'auth004-inventory/v1');
    assert.equal(r.project, browser, 'J02.L1.A13 project/engine mismatch');
    assert.equal(r.title, cell === 'landing' ? landingTitle : `AUTH-E2E-005: ${cell}`);
    assert.equal(r.retry, 0, 'J02.L1.A13 retries do not replace first-attempt proof');
    assert.equal(r.repeat, 0);
    assert.equal(JSON.stringify(r.rows.map(row => row.scenario)), JSON.stringify(cell === 'landing' ? NON_MAIL :
      cell === 'invite-link' ? ['invite-link', 'invite-explicit-expired'] : [cell]), 'J02.L1.A13 exact scenario inventory');
    assert.ok(r.rows.every(row => row.browser === browser && row.status === 'passed' && row.control === false &&
      row.checks.length > 0 && row.checks.every(check => check.passed === true) && row.boundary?.refused === 0 &&
      row.boundary?.unrouted === 0), 'J02.L1.A13 all named cases must pass without skipped/negative/unobserved rows');
    assert.equal(r.tenantDigests.length, 2);
    for (const tenant of r.tenantDigests) {
      assert.ok(/^[a-f0-9]{64}$/.test(tenant) && !tenants.has(tenant), 'J02.L1.A13 requires independent test-owned tenant sets');
      tenants.add(tenant);
    }
  }
  return { tests: 10, scenarios: 34, browsers: 2, complete: true };
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  // No discovery, service launch, credential paths, runtime mutation or new kit.
  // Exact argument paths are bound in the accepted Stage A consumer descriptor.
  try {
    const result = reconcileLandingInventory(process.argv.slice(2).map(path => JSON.parse(readFileSync(path, 'utf8'))));
    process.stdout.write(JSON.stringify(result) + '\n');
  } catch { process.stderr.write('AUTH004_INCOMPLETE_INVENTORY\n'); process.exitCode = 1; }
}
