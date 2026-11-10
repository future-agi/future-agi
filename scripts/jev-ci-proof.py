"""Temporary CI-only Jev expectation and route-drain probes; never part of the PR."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
old = root / 'e2e/flows/evals/jev-evaluator-models.spec.ts'
race = root / 'e2e/flows/evals/evaluator-picker-search.spec.ts'
product = root / 'frontend/src/sections/evals/components/ModelSelector.jsx'
originals = {path: path.read_text() for path in (old, race, product)}
out = root / 'e2e/jev-proof'
out.mkdir(parents=True, exist_ok=True)
receipts = []


def replace_once(text, before, after):
    assert text.count(before) == 1, f'Expected one mutation anchor: {before[:100]}'
    return text.replace(before, after, 1)


def restore():
    for path, source in originals.items():
        path.write_text(source)


def specs(suite):
    yield from suite.get('specs', [])
    for child in suite.get('suites', []):
        yield from specs(child)


def run(name, ids, expected='passed', message=None, drain=False):
    folder = out / name
    folder.mkdir()
    env = dict(os.environ, PLAYWRIGHT_JSON_OUTPUT_FILE=str(folder / 'report.json'),
               PLAYWRIGHT_HTML_OUTPUT_DIR=str(folder / 'html'))
    command = ['bin/e2e', 'test', str(old.relative_to(root)), str(race.relative_to(root)),
               '--grep', '|'.join(f'{id}:' for id in ids), '--reporter=json,html',
               '--trace=on', '--output', str(folder / 'results')]
    result = subprocess.run(command, cwd=root, env=env, timeout=900, check=False)
    report = json.loads((folder / 'report.json').read_text())
    tests = [(spec['title'], test) for suite in report['suites'] for spec in specs(suite)
             for test in spec.get('tests', [])]
    assert len(tests) == len(ids), (name, len(tests), ids)
    errors = []
    attachments = []
    for title, test in tests:
        assert len(test['results']) == 1, (name, title, 'unexpected retry')
        observation = test['results'][0]
        assert observation['retry'] == 0, (name, title, 'nonzero retry')
        assert observation['status'] == expected, (name, title, observation['status'])
        errors.extend(error.get('message', '') for error in observation.get('errors', []))
        attachments.extend(observation.get('attachments', []))
    text = re.sub(r'\x1b\[[0-9;]*m', '', '\n'.join(errors))
    if message:
        assert message in text, (name, 'wrong failure', text)
    assert result.returncode == (0 if expected == 'passed' else 1), (name, result.returncode)
    if drain:
        proof = [attachment for attachment in attachments if attachment['name'] == 'route-drain-proof']
        assert len(proof) == 1, (name, 'missing teardown witness')
        import base64
        payload = (Path(proof[0]['path']).read_text() if 'path' in proof[0]
                   else base64.b64decode(proof[0]['body']).decode())
        assert json.loads(payload) == {'pendingBeforeDrain': True, 'settledBeforeContextClose': True}
    receipts.append({'name': name, 'ids': ids, 'status': expected, 'retry': 0,
                     'expected_failure': text, 'route_drain_verified': drain})
    (out / 'summary.json').write_text(json.dumps(receipts, indent=2))
    print(f'PROOF {name}: {expected}; retries=0', flush=True)


def mutate(name, path, before, after, id, message):
    restore()
    path.write_text(replace_once(originals[path], before, after))
    run(name, [id], 'failed', message)


def pending_route_source(fail):
    source = originals[old]
    source = replace_once(source, "const ui = expect.configure({ timeout: UI_READY });", """const ui = expect.configure({ timeout: UI_READY });
let holdNextDecision = false;
let releasePending: (() => void) | undefined;
let enteredPending: () => void;
let pendingGate: Promise<void>;
let pendingSettled = false;""")
    source = replace_once(source, "test.afterEach(async ({ page }) => {\n  await page.unrouteAll({ behavior: 'wait' });\n});", """test.afterEach(async ({ page }, testInfo) => {
  const draining = page.unrouteAll({ behavior: 'wait' });
  if (releasePending) {
    expect(pendingSettled).toBe(false);
    releasePending();
  }
  await draining;
  if (releasePending) {
    expect(pendingSettled).toBe(true);
    await testInfo.attach('route-drain-proof', { contentType: 'application/json',
      body: JSON.stringify({ pendingBeforeDrain: true, settledBeforeContextClose: pendingSettled }) });
  }
});""")
    source = replace_once(source, "    const response = await route.fetch();\n    const body = await response.json()", """    const held = holdNextDecision;
    holdNextDecision = false;
    const response = await route.fetch();
    if (held) {
      enteredPending();
      await pendingGate;
    }
    const body = await response.json()""")
    source = replace_once(source, "    await fulfillEdited(route, response, body);", """    await fulfillEdited(route, response, body);
    if (held) pendingSettled = true;""")
    source = replace_once(source, "  await test.step('UI: reload shows Scoring and the four levels in order', async () => {\n    await page.reload", """  await test.step('UI: reload shows Scoring and the four levels in order', async () => {
    pendingGate = new Promise<void>(resolve => { releasePending = resolve; });
    const entered = new Promise<void>(resolve => { enteredPending = resolve; });
    holdNextDecision = true;
    await page.reload""")
    source = replace_once(source, "      { timeout: UI_READY }).toEqual(levels);\n  });", """      { timeout: UI_READY }).toEqual(levels);
    await entered;
""" + ("    expect('cleanup probe').toBe('deliberate failure');\n" if fail else '') + "  });")
    return source


baseline_tag = os.environ['IMAGE_TAG']
changed_image = False
try:
    run('baseline', ['EVAL-E2E-007', 'EVAL-E2E-008', 'EVAL-E2E-009', 'EVAL-E2E-010', 'EVAL-E2E-011', 'EVAL-E2E-013'])
    mutate('007-no-key', old,
           "\n  expect(typesafe(await actor.api.get<ProviderStatus>(PROVIDER_STATUS)).has_key).toBe(false);",
           "\n  expect(typesafe(await actor.api.get<ProviderStatus>(PROVIDER_STATUS)).has_key).toBe(true);",
           'EVAL-E2E-007', 'Expected: true')
    mutate('007-no-write', old, '    expect(keyWrites).toEqual([]);',
           "    expect(keyWrites).toEqual(['synthetic expected write']);", 'EVAL-E2E-007', 'synthetic expected write')
    mutate('010-pg-order', old,
           "expect(row.config.jev_mapping).toMatchObject({ question_type: 'score', score: { levels }, pass: null, choice: null });",
           "expect(row.config.jev_mapping).toMatchObject({ question_type: 'score', score: { levels: [levels[1], levels[0], ...levels.slice(2)] }, pass: null, choice: null });",
           'EVAL-E2E-010', 'Off topic')
    mutate('010-v1-snapshot', old,
           'expect(versions[0].config_snapshot.jev_mapping).toEqual(row.config.jev_mapping);',
           "expect(versions[0].config_snapshot.jev_mapping).toEqual({ ...row.config.jev_mapping, score: { levels: ['synthetic expected V1 level'] } });",
           'EVAL-E2E-010', 'synthetic expected V1 level')
    mutate('013-no-key', race, "provider: 'typesafe', has_key: false", "provider: 'typesafe', has_key: true",
           'EVAL-E2E-013', '"has_key": true')
    mutate('013-no-write', race, '      expect(keyWrites).toEqual([]);',
           "      expect(keyWrites).toEqual(['synthetic expected write']);", 'EVAL-E2E-013', 'synthetic expected write')
    for fail in (False, True):
        restore()
        old.write_text(pending_route_source(fail))
        run('010-pending-route-' + ('failure' if fail else 'success'), ['EVAL-E2E-010'],
            'failed' if fail else 'passed', 'deliberate failure' if fail else None, drain=True)
    restore()
    product.write_text(replace_once(originals[product], """  const openKeysDrawer = (nextModel) => {
    pendingModelRef.current = nextModel;
    setKeysDrawerModel(nextModel);
    setModelAnchor(null);
  };""", '  const openKeysDrawer = () => {};'))
    subprocess.run(['docker', 'buildx', 'build', '--load', '-t', 'futureagi/frontend:jev-drawer-negative',
                    '--cache-from', f"type=registry,ref={os.environ['CACHE_REPO']}:frontend", 'frontend'],
                   cwd=root, timeout=900, check=True)
    env = dict(os.environ, FRONTEND_VERSION='jev-drawer-negative')
    subprocess.run(['bin/e2e', 'compose', 'up', '-d', '--no-deps', '--force-recreate', 'frontend'],
                   cwd=root, env=env, timeout=180, check=True)
    changed_image = True
    run('007-disabled-drawer', ['EVAL-E2E-007'], 'failed', 'toBeHidden')
    restore()
    env = dict(os.environ, FRONTEND_VERSION=baseline_tag)
    subprocess.run(['bin/e2e', 'compose', 'up', '-d', '--no-deps', '--force-recreate', 'frontend'],
                   cwd=root, env=env, timeout=180, check=True)
    changed_image = False
    run('restored', ['EVAL-E2E-007', 'EVAL-E2E-008', 'EVAL-E2E-009', 'EVAL-E2E-010', 'EVAL-E2E-011', 'EVAL-E2E-013'])
finally:
    restore()
    if changed_image:
        subprocess.run(['bin/e2e', 'compose', 'up', '-d', '--no-deps', '--force-recreate', 'frontend'],
                       cwd=root, env=dict(os.environ, FRONTEND_VERSION=baseline_tag), timeout=180, check=False)
