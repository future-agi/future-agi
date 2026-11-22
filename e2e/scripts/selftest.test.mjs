import test from 'node:test';
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

test('the required catalog check runs the harness regressions once per stack; docs remain a separate fail-closed command', () => {
  const manifest = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
  const workflow = parse(readFileSync(new URL('../../.github/workflows/e2e-ci.yml', import.meta.url), 'utf8'));
  // catalog:check ends with the harness selftests. It runs on each stack's last
  // shard, and the once-per-stack record carries its outcome to the report job.
  const steps = workflow.jobs.e2e.steps;
  const step = steps.find(s => s.id === 'catalog');
  assert.equal(step.run, 'yarn catalog:check');
  assert.equal(step['working-directory'], 'e2e');
  assert.equal(step.if, "env.E2E_LAST_SHARD == 'true'");
  assert.equal(step['continue-on-error'], undefined);
  const record = steps.find(s => s.name === 'Record the once-per-stack steps');
  assert.equal(record.env.CATALOG, '${{ steps.catalog.outcome }}');
  assert.equal(manifest.scripts['catalog:check'], 'node scripts/flow-catalog.mjs --check && yarn selftest');
  assert.equal(manifest.scripts.selftest, 'node scripts/selftest.mjs');
  assert.equal(manifest.scripts['selftest:docs'], `node --test ${docsSuite}`);
  const docs = readFileSync(new URL(`../${docsSuite}`, import.meta.url), 'utf8');
  assert.match(docs, /if \(!root\) throw new Error/);
});
