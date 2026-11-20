import test from 'node:test';
import { spawnSync } from 'node:child_process';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { parse } from 'yaml';
import { discoverSelftests, docsSuite } from './selftest.mjs';

test('normal selftest discovers comparison, accounting and teardown regressions', () => {
  const paths = discoverSelftests();
  for (const suite of ['comparison-regressions', 'controls-regressions', 'ledger', 'benchmark', 'stage-coverage', 'git-controls']) {
    assert.ok(paths.includes(`harness/skills-r3/${suite}.test.mjs`), suite);
  }
  assert.ok(paths.includes('scripts/host-update-fallibility.test.mjs'));
  assert.equal(paths.includes(docsSuite), false);
});

test('discovery includes future nested suites and excludes only the explicit documentation gate', () => {
  const root = mkdtempSync(join(tmpdir(), 'e2e-selftest-'));
  try {
    const paths = ['scripts/legacy.test.mjs', 'scripts/nested/new.test.mjs',
      'harness/skills-r3/new.test.mjs', 'harness/skills-r3/nested/new.test.mjs',
      docsSuite, 'harness/skills-r3/helper.mjs', 'harness/skills-r3/browser.spec.ts'];
    for (const path of paths) {
      mkdirSync(join(root, path, '..'), { recursive: true });
      writeFileSync(join(root, path), '');
    }
    assert.deepEqual(discoverSelftests(root), paths.slice(0, 4).sort());
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('required cloud catalog gate runs harness regressions; docs remain a separate fail-closed command', () => {
  const manifest = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
  const workflow = parse(readFileSync(new URL('../../.github/workflows/e2e-ci.yml', import.meta.url), 'utf8'));
  const catalog = workflow.jobs['flow-catalog'];
  assert.equal(catalog.needs, 'changes');
  assert.equal(catalog.if, "needs.changes.outputs.any == 'true'");
  assert.equal(catalog['continue-on-error'], undefined);
  const step = catalog.steps.find(s => s.run?.trimEnd().split('\n').at(-1).trim() === 'yarn catalog:check');
  assert.ok(step, 'the catalog job must finish its shell step with catalog:check');
  assert.equal(step['working-directory'], 'e2e');
  assert.equal(step.if, undefined);
  assert.equal(step['continue-on-error'], undefined);
  const gate = workflow.jobs['e2e-tests'];
  assert.ok(gate.needs.includes('flow-catalog'));
  assert.equal(gate.if, 'always()');
  const outcome = gate.steps.find(s => s.env?.NEEDS === '${{ toJSON(needs) }}');
  assert.ok(outcome, 'aggregate must check dependency outcomes');
  assert.equal(outcome.if, undefined);
  assert.equal(outcome['continue-on-error'], undefined);
  const needs = Object.fromEntries(gate.needs.map(id => [id, { result: 'success' }]));
  needs.changes.outputs = { any: 'true' };
  for (const result of ['success', 'failure', 'skipped', 'cancelled']) {
    needs['flow-catalog'].result = result;
    const check = spawnSync('/bin/bash', ['-e', '-c', outcome.run], { env: { PATH: process.env.PATH, NEEDS: JSON.stringify(needs) } });
    assert.equal(check.status === 0, result === 'success', `aggregate with catalog result ${result}`);
  }
  assert.equal(manifest.scripts['catalog:check'], 'node scripts/flow-catalog.mjs --check && yarn selftest');
  assert.equal(manifest.scripts.selftest, 'node scripts/selftest.mjs');
  assert.equal(manifest.scripts['selftest:docs'], `node --test ${docsSuite}`);
  const docs = readFileSync(new URL(`../${docsSuite}`, import.meta.url), 'utf8');
  assert.match(docs, /if \(!root\) throw new Error/);
});
