import { chmodSync, mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test, expect } from '@playwright/test';
import { registeredValues } from '../lib/value-free-boundary.mjs';
import { privatePath, removePrivate } from './value-free/private-dir';
import { databaseNow, E2E_DIR, killChildren, rawIdentities, runChild } from './value-free/scan';

// Fixtures live in a private directory per test, outside the evidence tree (value-free/private-dir.ts).
test.afterEach(async ({}, testInfo) => removePrivate(testInfo));

// HR-JRN-01 round 10, live (Standalone stack): 708 addendum 68. The walk rig's own channels (the paths it lists and lands
// files under, its prepare output) carry the VM checkout's absolute path, and main ruled them out of the publication choke
// point's scope on one condition: every value a value-free run mints and registers is disjoint from that path and from
// each of its components, with no substring overlap either way. This mints them the normal way: value-free/mint.child.ts
// provisions the J03 actor set through the ScopeActors fixture under the run switch, with a registry this test gives the
// run (as the walk job gives one), and the choke point's own reader (registeredValues) reads back everything it
// registered. The checkout is this file's own, the path the rig mirrors on the VM. Overlaps are reported by length and
// component only, never the value.
test('value-free live: every value a value-free run mints and registers is disjoint from the checkout path and its components (708 add.68)',
  async ({}, testInfo) => {
    test.setTimeout(900_000);
    const checkout = path.resolve(E2E_DIR, '..');
    const components = checkout.split(path.sep).filter(Boolean);
    expect(components.length, 'the checkout path has components').toBeGreaterThan(0);
    const registries = mkdtempSync(path.join(os.tmpdir(), 'e2e-vf-disjoint-'));
    chmodSync(registries, 0o700);
    const registry = path.join(registries, 'run');
    mkdirSync(registry, { mode: 0o700 });
    const since = await databaseNow();
    let values: string[] | null = null;
    try {
      const run = await runChild('value-free, the registry given', privatePath(testInfo, 'run'),
        { file: 'value-free/mint.child.ts', valueFree: true, probes: {}, env: { E2E_VALUE_FREE_REGISTRY: registry } });
      expect(run.code, `the minting run (stats ${JSON.stringify(run.results?.stats ?? null)})`).toBe(0);
      values = registeredValues(registries);
    } finally {
      killChildren();
      rmSync(registries, { recursive: true, force: true });
    }
    expect(values, 'the registry, read whole by the choke point').not.toBeNull();
    // What the run minted is in it: every email, user id, org name and workspace name Postgres holds for its actors.
    const raw = await rawIdentities(since);
    expect(raw.users.length, 'the J03 set: five actors').toBeGreaterThanOrEqual(5);
    const minted = [...raw.users.flatMap((u) => [u.email, u.id]), ...raw.orgNames.map((o) => o.name),
      ...raw.workspaces.map((w) => w.name)].filter(Boolean);
    expect(minted.filter((v) => !values!.includes(v)).length, 'every minted identity is registered').toBe(0);
    // 708 add.68, both ways and case folded: no value inside the checkout path (one component, or across them), and no
    // component inside a value.
    const folded = checkout.toLowerCase();
    const overlaps = values!.flatMap((v) => [
      ...(folded.includes(v.toLowerCase()) ? [{ length: v.length, overlap: 'a value inside the checkout path' }] : []),
      ...components.filter((c) => v.toLowerCase().includes(c.toLowerCase()))
        .map((c) => ({ length: v.length, overlap: `the component ${c} inside a value` })),
    ]);
    expect(overlaps, `registered values overlapping the checkout path ${checkout}`).toEqual([]);
  });
