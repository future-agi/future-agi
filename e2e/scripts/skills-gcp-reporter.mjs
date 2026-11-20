// Playwright evidence for exactly one owned case. Never serialize TestResult
// itself: errors, stdout, attachments and trace paths can contain actor keys.
import { readFileSync, writeFileSync } from 'node:fs';
import { validateProvenance } from '../benchmarks/context-hub/runner.mjs';
import { join, relative } from 'node:path';
import { homedir } from 'node:os';

// The GCP input and reports stay private. Copy only source identities and
// digests, not free-form manifest fields or failed-driver messages.
function evidence(name, { status, provenance }) {
  validateProvenance(provenance);
  if (provenance.environment !== 'gcp') throw new Error('Expected a GCP registration');
  const { product_base, product_head, product_tree, sdk_commit, runtime_digest, fixture_hash, package_hashes, lockfile_hashes, dirty } = provenance;
  return { name, status, product_base, product_head, product_tree, sdk_commit, runtime_digest, fixture_hash, package_hashes, lockfile_hashes, dirty };
}

export default class SkillsGcpReporter {
  onBegin(config, suite) {
    this.root = config.rootDir;
    this.tests = suite.allTests();
  }

  onEnd(run) {
    const tests = this.tests ?? [];
    const [test] = tests;
    const result = test?.results[0];
    const id = process.env.SKILLS_GCP_CASE;
    const title = process.env.SKILLS_GCP_TITLE;
    const matches = test && relative(this.root, test.location.file) === process.env.SKILLS_GCP_SPEC
      && (id === 'j3' || id === 'j4' ? test.title === title : test.title.startsWith(`SKILLS-E2E-${id}:`));
    const names = id === 'j4' ? ['cold', 'configured'] : id === 'j3' ? ['configured'] : id === '063' ? ['guest'] : ['campaign'];
    const records = [];
    for (const name of names) {
      try {
        if (name === 'campaign') {
          records.push(evidence(name, { status: 'registered', provenance: JSON.parse(readFileSync(process.env.J5_PROVENANCE_JSON)) }));
        } else {
          const a = result?.attachments.find((item) => item.name === `registration-${name}`);
          if (a) records.push(evidence(name, JSON.parse(a.body ?? readFileSync(a.path))));
        }
      } catch { /* An incomplete registration is not evidence. Keep partial diagnostics. */ }
    }
    const passed = records.length === names.length && (!(id === 'j3' || id === 'j4') || records.every(r => r.status === 'completed')) && Boolean(matches && tests.length === 1 && run.status === 'passed' && test.expectedStatus === 'passed'
      && test.results.length === 1 && result.retry === 0 && result.status === 'passed');
    writeFileSync(join(homedir(), 'result.json'), JSON.stringify({ case: id, sha: process.env.SKILLS_GCP_HEAD,
      pair_sha256: process.env.SKILLS_GCP_PAIR_SHA256, count: tests.length, passed, records, duration_ms: result?.duration ?? 0, error_line: result?.error?.location?.line ?? 0 }), { mode: 0o600 });
    if (!passed) return { status: 'failed' };
  }
}
