import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, copyFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { execFileSync, spawnSync } from 'node:child_process';

// The app usually sits at the git root, but a checkout can also nest it in a
// subdirectory such as core/. Run the real CLI against a real git diff in both
// layouts, not just classifyChange with already-normalized synthetic paths.
for (const prefix of ['', 'core']) {
  for (const scenario of ['new-page', 'accounting-migration']) {
    test(`coverage CLI blocks ${scenario} in ${prefix || 'root'} layout`, t => {
      const root = mkdtempSync(path.join(tmpdir(), 'gateway-coverage-'));
      t.after(() => rmSync(root, { recursive: true, force: true }));
      const app = path.join(root, prefix);
      const env = { ...process.env, GIT_AUTHOR_NAME: 'Coverage Fixture', GIT_AUTHOR_EMAIL: 'coverage@example.com',
        GIT_COMMITTER_NAME: 'Coverage Fixture', GIT_COMMITTER_EMAIL: 'coverage@example.com' };
      const git = (...args) => execFileSync('git', ['-C', root, ...args], { env, encoding: 'utf8' }).trim();
      const write = (relative, text) => {
        const file = path.join(app, relative); mkdirSync(path.dirname(file), { recursive: true }); writeFileSync(file, text);
      };
      git('init', '-q');
      write('e2e/FLOWS.md', '# E2E flows\n');
      mkdirSync(path.join(app, 'e2e/scripts'), { recursive: true });
      copyFileSync(new URL('./e2e-coverage.mjs', import.meta.url), path.join(app, 'e2e/scripts/e2e-coverage.mjs'));
      git('add', '.');
      git('commit', '-qm', 'Synthetic coverage baseline');
      const base = git('rev-parse', 'HEAD');
      if (scenario === 'new-page') {
        write('frontend/src/pages/gateway/accounting.jsx', 'export default function Accounting() { return null; }\n');
      } else {
        write('futureagi/agentcc/migrations/0029_request_log_usage_state.py',
          'from django.db import migrations\noperations = [migrations.AddField(name="usage_state")]\n');
        write('futureagi/agentcc/serializers/request_log.py', 'fields = ["usage_state"]\n');
      }
      git('add', '.');
      git('commit', '-qm', 'Synthetic user-visible change');
      // A declaration cannot waive a new surface. This also pins the exact
      // block verdict rather than the separate missing-marker result.
      const body = path.join(root, 'pr-body.txt');
      writeFileSync(body, 'E2E: exempt (unclassified)\n');
      const result = spawnSync(process.execPath, [path.join(app, 'e2e/scripts/e2e-coverage.mjs'),
        '--base', base, '--title', 'fix(gateway): preserve native usage', '--body', body, '--json'], { env, encoding: 'utf8' });
      assert.equal(result.stderr, '');
      const report = JSON.parse(result.stdout);
      assert.equal(report.classification, 'NEW-FLOW');
      assert.equal(report.verdict, 'block');
      assert.equal(result.status, 1);
      assert.ok(report.files.every(file => !file.path.startsWith('core/')));
    });
  }
}
