import { randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { COMMIT, SHA256, exactSet, requireValue, sha256 } from './canonical.mjs';

export function validateNativeAttestation(value) {
  requireValue(value?.schema === 1, 'Native attestation schema 1 required');
  requireValue(value.environment?.kind === 'gcp' && value.environment.project === 'fagi-omega-staging', 'Task-owned GCP control plane required');
  requireValue(value.environment.labels?.purpose === 'ctxhub-test' && typeof value.environment.labels.owner === 'string', 'Ownership labels missing');
  requireValue(Date.parse(value.environment.expires_at) > Date.now(), 'Task environment expired');
  requireValue(value.product?.dirty === false && value.product.synthetic_gitify !== true, 'Real committed refs required, not gitify/dirty iteration');
  for (const key of ['base', 'head', 'tree']) requireValue(COMMIT.test(value.product[key]), `Product ${key} missing`);
  requireValue(COMMIT.test(value.sdk?.commit) && SHA256.test(value.sdk?.wheel_sha256) && SHA256.test(value.sdk?.npm_sha256), 'Exact SDK packages required');
  requireValue(value.guest?.provider === 'e2b' && SHA256.test(value.guest.template_digest) && SHA256.test(value.guest.runtime_digest), 'Qualified guest/template/runtime required');
  requireValue(value.adapter?.module === 'fi.context.adapters.simulation_entrypoint' && SHA256.test(value.adapter.application_wheel_sha256), 'Actual packaged Simulation adapter required');
  requireValue(value.adapter.protocol === '1' && value.adapter.launch_path === 'simulation', 'Do not replace Simulation with a test subprocess');
  requireValue(value.guest.source_read_only === true && value.guest.context_read_only === true && value.guest.outputs_separate === true, 'Guest mount separation required');
  requireValue(value.auth?.mode === 'context_only' && value.auth.broad_keys_absent === true, 'Isolated Context credential mode required');
  const health = new URL(value.health_url);
  requireValue(['127.0.0.1', 'localhost', '[::1]'].includes(health.hostname) && ['http:', 'https:'].includes(health.protocol) && !health.username && !health.password && !health.search, 'Health must use a task-owned local authenticated relay, not an arbitrary public URL');
  requireValue(SHA256.test(value.fixture_sha256) && Array.isArray(value.lockfile_hashes) && value.lockfile_hashes.length > 0 && value.lockfile_hashes.every((hash) => SHA256.test(hash)), 'Fixture/lock provenance missing');
  exactSet(value.guest.allowed_egress_roles, ['fake_provider', 'authenticated_ingestion'], 'guest egress');
  return value;
}

export async function readBoundedHealth(response) {
  requireValue(response.body, 'Health body missing');
  const reader = response.body.getReader();
  const chunks = []; let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      requireValue(size <= 64 * 1024, 'Health receipt too large');
      chunks.push(Buffer.from(value));
    }
  } finally { await reader.cancel(); reader.releaseLock(); }
  return Buffer.concat(chunks).toString('utf8');
}

/** Read actual local refs on the cloud host and challenge the already launched guest. */
export async function qualifyNative({ attestationPath, root, authToken, fetchImpl = fetch }) {
  requireValue(process.platform === 'linux', 'Native qualification runs on approved cloud Linux only');
  requireValue(authToken && authToken.length >= 32, 'Narrow relay credential missing');
  const raw = await readFile(attestationPath, 'utf8');
  let parsed;
  try { parsed = JSON.parse(raw); } catch { throw new Error('Invalid native attestation JSON'); }
  const attestation = validateNativeAttestation(parsed);
  const git = (...args) => execFileSync('git', ['-C', root, ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
  requireValue(git('rev-parse', 'HEAD') === attestation.product.head && git('rev-parse', 'HEAD^{tree}') === attestation.product.tree, 'Cloud checkout differs from attestation');
  requireValue(git('status', '--porcelain') === '', 'Cloud checkout dirty');
  requireValue(git('rev-parse', attestation.product.base) === attestation.product.base, 'Base ref missing');
  const nonce = randomUUID();
  const response = await fetchImpl(attestation.health_url, { method: 'POST', redirect: 'error',
    headers: { authorization: `Bearer ${authToken}`, 'content-type': 'application/json' },
    body: JSON.stringify({ nonce, protocol: '1' }), signal: AbortSignal.timeout(15_000) });
  requireValue(response.ok, 'Native health failed');
  const body = await readBoundedHealth(response);
  let receipt;
  try { receipt = JSON.parse(body); } catch { throw new Error('Invalid native health JSON'); }
  requireValue(receipt.nonce === nonce && receipt.protocol === '1' && receipt.ready === true, 'Guest challenge failed');
  for (const [key, expected] of Object.entries({ template_digest: attestation.guest.template_digest,
    runtime_digest: attestation.guest.runtime_digest, sdk_wheel_sha256: attestation.sdk.wheel_sha256,
    application_wheel_sha256: attestation.adapter.application_wheel_sha256 })) requireValue(receipt[key] === expected, `Guest ${key} mismatch`);
  requireValue(receipt.context_read_only === true && receipt.source_read_only === true && receipt.outputs_separate === true, 'Live mount attestation missing');
  exactSet(receipt.allowed_egress_roles, ['fake_provider', 'authenticated_ingestion'], 'live egress');
  return { qualified: true, attestation_sha256: sha256(raw), health_receipt_sha256: sha256(body),
    product_head: attestation.product.head, scope: 'topology_only_not_customer_behavior' };
}
