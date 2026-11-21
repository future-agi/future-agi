import test from 'node:test';
import assert from 'node:assert/strict';
import { readBoundedHealth, validateNativeAttestation } from '../../lib/skills-r3/native-preflight.mjs';
import nativeSetup from './native-setup.mjs';

const hash = 'a'.repeat(64); const commit = 'b'.repeat(40);
function sample() {
  return { schema: 1,
    environment: { kind: 'gcp', project: 'fagi-omega-staging', labels: { owner: 'synthetic', purpose: 'ctxhub-test' }, expires_at: new Date(Date.now() + 3600_000).toISOString() },
    product: { base: commit, head: commit, tree: commit, dirty: false },
    sdk: { commit, wheel_sha256: hash, npm_sha256: hash },
    guest: { provider: 'e2b', template_digest: hash, runtime_digest: hash, source_read_only: true, context_read_only: true, outputs_separate: true, allowed_egress_roles: ['fake_provider', 'authenticated_ingestion'] },
    adapter: { module: 'fi.context.adapters.simulation_entrypoint', application_wheel_sha256: hash, protocol: '1', launch_path: 'simulation' },
    auth: { mode: 'context_only', broad_keys_absent: true }, health_url: 'http://127.0.0.1:34567/owned/health', fixture_sha256: hash, lockfile_hashes: [hash] };
}

test('native configuration is explicitly fail-closed when runner/credentials are absent', async () => {
  const previous = process.env.CTXHUB_NATIVE_ATTESTATION;
  delete process.env.CTXHUB_NATIVE_ATTESTATION;
  try { await assert.rejects(nativeSetup(), /not a skipped green flow/); }
  finally { if (previous !== undefined) process.env.CTXHUB_NATIVE_ATTESTATION = previous; }
});

test('health response rejects oversized actual bytes without trusting Content-Length', async () => {
  assert.equal(await readBoundedHealth(new Response('{"ready":true}')), '{"ready":true}');
  await assert.rejects(readBoundedHealth(new Response('x'.repeat(65537), { headers: { 'content-length': '1' } })), /too large/);
});

test('attestation validates actual package, mount, auth, egress and real-ref prerequisites', () => {
  assert.equal(validateNativeAttestation(sample()).schema, 1);
  const mutations = [
    (s) => { s.environment.project = 'production'; }, (s) => { s.environment.expires_at = '2020-01-01'; },
    (s) => { s.product.synthetic_gitify = true; }, (s) => { s.product.dirty = true; },
    (s) => { s.product.head = 'moving-branch'; }, (s) => { s.sdk.wheel_sha256 = null; },
    (s) => { s.guest.template_digest = null; }, (s) => { s.guest.context_read_only = false; },
    (s) => { s.adapter.launch_path = 'test-subprocess'; }, (s) => { s.auth.mode = 'broad_pair'; },
    (s) => { s.health_url = 'https://external.invalid/'; }, (s) => { s.health_url = 'http://localhost/?token=secret'; },
    (s) => { s.guest.allowed_egress_roles.push('arbitrary_network'); }, (s) => { s.lockfile_hashes = []; },
  ];
  for (const mutate of mutations) { const value = sample(); mutate(value); assert.throws(() => validateNativeAttestation(value)); }
});
