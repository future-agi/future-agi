"""Scratch-validation branch only: real managed EVAL006 proof, never product CI.
Runs the unchanged stack command with one flow; negative mutates only the CH
expectation and must fail at that exact assertion, not during setup.
"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / 'e2e/flows/evals/task-usage-trace-drawer.spec.ts'
OUT = ROOT / 'e2e/eval006-proof-reports'
PHASE = sys.argv[1]
if PHASE not in ('green', 'negative', 'restored'):
    raise SystemExit('Unknown proof phase')
OUT.mkdir(exist_ok=True)
phase_dir = OUT / PHASE
phase_dir.mkdir(exist_ok=False)
source = SPEC.read_bytes()
source_sha = hashlib.sha256(source).hexdigest()
manifest_path = OUT / 'source.json'
if PHASE == 'green':
    manifest_path.write_text(json.dumps({
        'commit': os.environ['GITHUB_SHA'],
        'stack': os.environ['E2E_STACK'],
        'spec_sha256': source_sha,
        'run_id': os.environ['GITHUB_RUN_ID'],
        'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }, indent=2) + '\n')
else:
    manifest = json.loads(manifest_path.read_text())
    if manifest['spec_sha256'] != source_sha:
        raise SystemExit('Source changed between managed proof phases')

anchor = b'const expectedChExplanation = `${verdict} saw llm`; // EVAL006_CDC_EXPECTATION_ONLY'
wrong = b'const expectedChExplanation = `${verdict}-WRONG saw llm`; // EVAL006_CDC_EXPECTATION_ONLY'
mutation_line = None
if PHASE == 'negative':
    if source.count(anchor) != 1:
        raise SystemExit('Expected exactly one expectation-only mutation anchor')
    mutated = source.replace(anchor, wrong)
    mutation_line = next(i + 2 for i, line in enumerate(mutated.decode().splitlines()) if 'EVAL006_CDC_EXPECTATION_ONLY' in line)
    SPEC.write_bytes(mutated)

command = ['bin/e2e', 'test', 'flows/evals/task-usage-trace-drawer.spec.ts',
           '--workers=1', '--reporter=line,html,json']
environ = os.environ.copy()
environ['PLAYWRIGHT_JSON_OUTPUT_NAME'] = str(phase_dir / 'results.json')
environ['PLAYWRIGHT_HTML_OUTPUT_DIR'] = str(phase_dir / 'html')
receipt = {
    'phase': PHASE, 'command': command, 'original_spec_sha256': source_sha,
    'executed_spec_sha256': hashlib.sha256(SPEC.read_bytes()).hexdigest(),
    'commit': os.environ['GITHUB_SHA'], 'stack': os.environ['E2E_STACK'],
    'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
}
try:
    with (phase_dir / 'command.log').open('w') as log:
        process = subprocess.run(command, cwd=ROOT, env=environ, stdout=log,
                                 stderr=subprocess.STDOUT, timeout=780)
        receipt['exit_code'] = process.returncode
finally:
    # Even an interrupted negative step cannot leave a weakened/modified spec.
    SPEC.write_bytes(source)
    receipt['restored_spec_sha256'] = hashlib.sha256(SPEC.read_bytes()).hexdigest()
    receipt['finished_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    (phase_dir / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')

report_path = phase_dir / 'results.json'
if not report_path.exists():
    raise SystemExit('No Playwright results: setup/infrastructure failure, not proof')
report = json.loads(report_path.read_text())

def tests(suites):
    for suite in suites:
        for spec in suite.get('specs', []):
            yield from spec.get('tests', [])
        yield from tests(suite.get('suites', []))

selected = list(tests(report.get('suites', [])))
if len(selected) != 1:
    raise SystemExit(f'Expected exactly one executed test, found {len(selected)}')
results = selected[0].get('results', [])
if len(results) != 1:
    raise SystemExit('Proof must contain one attempt, with no retry')
result = results[0]
if PHASE == 'negative':
    error = result.get('error', {})
    message = re.sub(r'\x1b\[[0-9;]*m', '', error.get('message', ''))
    stack = re.sub(r'\x1b\[[0-9;]*m', '', error.get('stack', ''))
    intended = (
        process.returncode != 0 and result.get('status') == 'failed'
        and '-WRONG saw llm' in message and 'Expected:' in message
        and re.search(r'Received:.*e2e-usage-drawer-verdict-[^\n]* saw llm', message)
        and not re.search(r'Received:[^\n]*-WRONG', message)
        and f'task-usage-trace-drawer.spec.ts:{mutation_line}:' in (message + stack)
    )
    if not intended:
        raise SystemExit('Negative run did not fail at the intended CH explanation assertion; inspect private report')
    (phase_dir / 'intended-failure.txt').write_text(message + '\n')
    receipt['intended_negative_failure'] = True
    receipt['assertion_line'] = mutation_line
    print(message)
else:
    if process.returncode != 0 or result.get('status') != 'passed':
        raise SystemExit(f'{PHASE} managed EVAL006 was not green; inspect phase report')
    receipt['passed'] = True
receipt['playwright_stats'] = report.get('stats', {})
(phase_dir / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps(receipt, indent=2))
