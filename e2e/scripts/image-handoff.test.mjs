import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { parse } from 'yaml';

const root = new URL('../../', import.meta.url);
const workflow = (name) => parse(readFileSync(new URL(`.github/workflows/${name}.yml`, root), 'utf8'));

test('image handoff verifier accepts matching bytes and rejects broken bindings', () => {
  execFileSync('python3', ['-I', '-B', fileURLToPath(new URL('.github/scripts/test_image_handoff.py', root))], { stdio: 'inherit' });
});

test('proof remains dispatch-only and uses the distributed backend recipe', () => {
  const proof = workflow('e2e-image-handoff-proof');
  assert.equal(proof.on, 'workflow_dispatch');
  const ordinary = workflow('e2e-ci');
  const base = (steps) => steps.find(s => s.with?.file === 'futureagi/Dockerfile.oss').with;
  const reference = base(ordinary.jobs.e2e.steps);
  const donor = base(proof.jobs.donor.steps);
  for (const key of ['context', 'file', 'load', 'target']) assert.equal(donor[key], reference[key], key);
  // Only this known conditional supplies no build arguments on Distributed.
  const slimOnly = "${{ matrix.stack == 'standalone' && 'IMAGE_VARIANT=slim' || '' }}";
  const distributedArgs = reference['build-args'] === slimOnly ? '' : (reference['build-args'] ?? '');
  assert.equal(donor['build-args'] ?? '', distributedArgs, 'Distributed build arguments');
  assert.equal(distributedArgs, ''); // standard variant is the Dockerfile default
  // Without a platforms input, ordinary CI builds for its hosted runner's architecture.
  if (reference.platforms === undefined) assert.equal(ordinary.jobs.e2e['runs-on'], 'ubuntu-latest');
  assert.equal(donor.platforms, reference.platforms ?? 'linux/amd64', 'Distributed platform');
  assert.equal(donor.platforms, 'linux/amd64');
  assert.equal(donor.tags, 'futureagi/future-agi:e2e-ci');
  assert.equal(donor['cache-from'], `type=registry,ref=${ordinary.env.CACHE_REPO}:backend`);
  assert.equal(donor['cache-to'], undefined);
  assert.equal(donor.push, false);
  const overlay = (steps) => steps.find(s => s.run?.includes('-f e2e/stack/backend/Dockerfile')).run
    .replaceAll('"', '').replaceAll('$IMAGE_TAG', 'e2e-ci').replace('--platform linux/amd64', '')
    .replaceAll('\\\n', ' ').replace(/\s+/g, ' ').trim();
  assert.equal(overlay(proof.jobs.donor.steps), overlay(ordinary.jobs.e2e.steps));
});
