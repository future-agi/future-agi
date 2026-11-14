import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parse } from 'yaml';
import { withLocalEditor } from '../stack/frontend/rewrite-index.mjs';

test('local editor fixture preserves the app entry and loads the exact editor before it', () => {
  const input = '<html><script src="/config.js"></script><script type="module" crossorigin src="/assets/index-ab12.js"></script><body><div id="root"></div></body></html>';
  const result = withLocalEditor(input);
  assert.equal(result.html, input.replace('/assets/index-ab12.js', '/e2e-monaco/bootstrap.js'));
  assert.match(result.bootstrap, /script.src = '\/e2e-monaco\/vs\/loader.js'/);
  assert.match(result.bootstrap, /window.require.config\(\{ paths: \{ vs: '\/e2e-monaco\/vs' \} \}\)/);
  assert.ok(result.bootstrap.indexOf("window.require(['vs/editor/editor.main']") < result.bootstrap.indexOf('await import('));
  assert.ok(result.bootstrap.endsWith('await import("/assets/index-ab12.js");\n'));
  assert.doesNotMatch(result.bootstrap, /https?:\/\//);
});

for (const [name, html] of [
  ['missing', '<html></html>'],
  ['multiple', '<script type="module" src="/assets/a.js"></script><script type="module" src="/assets/b.js"></script>'],
  ['remote', '<script type="module" src="https://external.invalid/a.js"></script>'],
  ['already wrapped', '<script type="module" src="/e2e-monaco/bootstrap.js"></script>'],
]) {
  test(`local editor fixture refuses ${name} entry modules`, () => {
    assert.throws(() => withLocalEditor(html), /Expected one local Vite entry module/);
  });
}

test('both CI topologies apply local editor assets before standalone assembly', () => {
  const workflow = parse(readFileSync(new URL('../../.github/workflows/e2e-ci.yml', import.meta.url), 'utf8'));
  const steps = workflow.jobs.e2e.steps;
  const index = steps.findIndex(s => s.name === 'Add local editor assets to the E2E frontend');
  assert.ok(index >= 0);
  assert.equal(steps[index].if, "env.BUILD_FRONTEND == 'true'");
  assert.match(steps[index].run, /-f e2e\/stack\/frontend\/Dockerfile/);
  assert.ok(index < steps.findIndex(s => s.name === 'Assemble futureagi/standalone from those images'));
  const dockerfile = readFileSync(new URL('../stack/frontend/Dockerfile', import.meta.url), 'utf8');
  assert.match(dockerfile, /npm pack monaco-editor@0\.43\.0 --ignore-scripts/);
  assert.match(dockerfile, /createHash\('sha512'\)/);
  assert.match(dockerfile, /Monaco integrity mismatch/);
});
