"""Scratch-branch-only managed proof: green, three one-sided mutations, green."""
from pathlib import Path
import json
import shutil
import subprocess

spec = Path('e2e/flows/observe/copilot-agent-usage-timing.spec.ts')
original = spec.read_text()
out = Path('e2e/copilot-fallibility')
out.mkdir(exist_ok=True)
mutations = [
    ('green-before', None, None, None),
    ('storage-kind', 'agent: 1, llm: 5, internal: timingIds', 'agent: 2, llm: 5, internal: timingIds', '"agent": 2'),
    ('usage-total', 'cases.map(c => [c.traceId, c.tokens]).sort()', 'cases.map(c => [c.traceId, c.tokens + 1]).sort()', '88074'),
    ('retained-timing', '.filter(s => s.observation_type === \'internal\').map(s => s.id).sort()).toEqual(timingIds);', '.filter(s => s.observation_type === \'internal\').map(s => s.id).sort()).toEqual([...timingIds, "ffffffffffffffff"].sort());', 'ffffffffffffffff'),
    ('green-after', None, None, None),
]
results = []
try:
    for name, old, new, witness in mutations:
        if old:
            if old not in original:
                raise RuntimeError('mutation anchor not found: ' + name)
            spec.write_text(original.replace(old, new, 1))
        else:
            spec.write_text(original)
        run = subprocess.run(['bin/e2e', 'test', 'flows/observe/copilot-agent-usage-timing.spec.ts', '--workers=1'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=540)
        dest = out / name
        dest.mkdir()
        (dest / 'test.log').write_text(run.stdout)
        report = Path('e2e/playwright-report')
        if report.exists():
            shutil.copytree(report, dest / 'report')
        passed = run.returncode == 0 if witness is None else run.returncode != 0 and witness in run.stdout and 'toEqual' in run.stdout
        results.append({'case': name, 'exit_code': run.returncode, 'expected_failure': witness is not None, 'witness': witness, 'proved': passed})
        (out / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
        print(json.dumps(results[-1]), flush=True)
        if not passed:
            print(run.stdout[-12000:], flush=True)
            raise SystemExit(1)
finally:
    spec.write_text(original)
