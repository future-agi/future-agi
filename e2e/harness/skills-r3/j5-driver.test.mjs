import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createDriver, requireNativeDelivery } from '../../benchmarks/context-hub/drivers/j5.mjs';
import { assertCodexBoundary, observeCodexLoad } from '../../lib/skills-r3/j5-native-fixture.mjs';

const ids = Array.from({ length: 33 }, (_, index) => String(1000 + index));
const installed = ids.map((id) => ({ repository_id: id, version_id: 'synthetic-v1', installed: true,
  discovery: 'not_observed', delivery: 'not_observed', source: 'filesystem' }));

test('verified installation and merge are not native discovery or delivery', () => {
  assert.throws(() => requireNativeDelivery(installed, ids, 'synthetic-v1'), /native_discovery_unobserved/);
  const discovered = installed.map((row) => ({ ...row, discovery: 'observed', merged: true }));
  assert.throws(() => requireNativeDelivery(discovered, ids, 'synthetic-v1'), /native_delivery_unobserved/);
});

test('a filesystem receipt cannot be relabelled as native load', () => {
  const wrongSource = installed.map((row) => ({ ...row, discovery: 'observed', delivery: 'observed' }));
  assert.throws(() => requireNativeDelivery(wrongSource, ids, 'synthetic-v1'), /installation_is_not_delivery/);
});

test('a missing target, wrong version or missing artifact cannot qualify load', () => {
  assert.throws(() => requireNativeDelivery(installed.slice(1), ids, 'synthetic-v1'), /native_delivery_target_set/);
  assert.throws(() => requireNativeDelivery(installed, ids, 'synthetic-v2'), /native_delivery_version/);
  const noArtifact = installed.map((row) => ({ ...row, discovery: 'observed', delivery: 'observed', source: 'model_boundary' }));
  assert.throws(() => requireNativeDelivery(noArtifact, ids, 'synthetic-v1'), /native_delivery_artifact_missing/);
});

test('empty or duplicated populations cannot qualify native delivery', () => {
  assert.throws(() => requireNativeDelivery([], [], 'synthetic-v1'), /native_delivery_included_population/);
  // Only 33 followers, or those plus the four pins adopted at their release.
  assert.throws(() => requireNativeDelivery(installed.slice(1), ids.slice(1), 'synthetic-v1'), /native_delivery_included_population/);
  assert.throws(() => requireNativeDelivery(installed, [...ids.slice(1), ids[1]], 'synthetic-v1'), /native_delivery_duplicate_target/);
});

test('a model reply and successful turn cannot substitute for captured instructions', () => {
  const observation = { requests: [{ input: [{ role: 'user', content: [{ type: 'input_text', text: 'Reply with acknowledgement.' }] }] }],
    events: [{ type: 'turn.completed' }], expectedInstructions: 'Retain original local instruction.\n' };
  assert.throws(() => assertCodexBoundary(observation), /native_instruction_delivery_missing/);
  assert.throws(() => assertCodexBoundary({ ...observation, requests: [] }), /unexpected_native_calls/);
  assert.throws(() => assertCodexBoundary({ ...observation, events: [{ type: 'turn.failed' }] }), /native_turn_not_completed/);
});

test('an unqualified native executable is refused before starting a process', async () => {
  await assert.rejects(observeCodexLoad({ executable: '/nonexistent/codex', executableSha256: '0'.repeat(64) }), /unqualified_native_binary/);
});

test('synthetic campaign driver cannot be selected for customer comparison evidence', async () => {
  await assert.rejects(createDriver({ jobId: 'J5', runId: 'j5-test', registration: {
    evidence_scope: 'customer', driver_config: { campaign_fixture: true },
  } }), /synthetic_fixture_cannot_claim_customer_evidence/);
});

test('legacy inventory registration remains non-mutating and unqualified', async () => {
  const driver = await createDriver({ jobId: 'J5', runId: 'j5-test', registration: { driver_config: {
    python: '/nonexistent/python', manifest_path: '/nonexistent/selected.json', expected_path: '/nonexistent/expected.json', evidence_path: '/nonexistent/evidence.json',
  } } });
  await assert.rejects(driver.perform({ id: 'establish-ownership' }, { signal: new AbortController().signal }), /j5_incomplete/);
  assert.deepEqual(await driver.teardown(), { qualified: false });
});
