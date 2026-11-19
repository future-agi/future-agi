import test from 'node:test';
import assert from 'node:assert/strict';
import { ActionLedger, summarizeLedger, verifyLedger } from '../../lib/skills-r3/ledger.mjs';
import { digest } from '../../lib/skills-r3/canonical.mjs';

const hash = 'a'.repeat(64);
const action = { dimension: 'A', primitive: 'command', actor: 'operator', step: 'create-prs', phase: 'recurring', target: 'batch' };
const receipt = { step: 'second-update', phase: 'recurring', source: 'git', artifactHash: hash, actionSequences: [1] };
const ledger = () => new ActionLedger({ runId: 'stage-test', job: 'J5', mode: 'selftest' });

test('stage receipts share an action and count automatic work as zero A/S/H/R', async () => {
  const journal = ledger();
  await journal.measure(action, () => {});
  journal.stage(receipt);
  journal.stage({ ...receipt, step: 'create-prs' });
  journal.stage({ ...receipt, step: 'reconcile-before-retry', phase: 'recovery', actionSequences: [] });
  assert.deepEqual(summarizeLedger(journal.events()).counts, { A: 1, S: 0, H: 0, R: 0 });
  assert.equal(verifyLedger(journal.events()).complete, true);
});

test('coverage refuses missing, failed, unfinished, duplicate and cross-phase action references', async () => {
  const journal = ledger();
  assert.throws(() => journal.stage(receipt), /same-phase/);
  await assert.rejects(journal.measure(action, () => { throw new Error('synthetic failure'); }));
  assert.throws(() => journal.stage(receipt), /completed/);
  let release;
  const pending = journal.measure(action, () => new Promise((resolve) => { release = resolve; }));
  assert.throws(() => journal.stage({ ...receipt, actionSequences: [3] }), /completed/);
  release(); await pending;
  assert.throws(() => journal.stage({ ...receipt, phase: 'recovery', actionSequences: [3] }), /same-phase/);
  assert.throws(() => journal.stage({ ...receipt, actionSequences: [3, 3] }), /Unique/);
  journal.stage({ ...receipt, actionSequences: [3] });
  assert.throws(() => journal.stage({ ...receipt, actionSequences: [3] }), /Duplicate/);
});

test('stage evidence cannot be a toast, missing artifact or screen-transition event', async () => {
  const journal = ledger();
  await journal.measure({ ...action, dimension: 'S', primitive: 'transition' }, () => {});
  assert.throws(() => journal.stage(receipt), /operator action/);
  assert.throws(() => journal.stage({ ...receipt, actionSequences: [], source: 'product_toast' }), /Independent/);
  assert.throws(() => journal.stage({ ...receipt, actionSequences: [], artifactHash: '' }), /Independent/);
});

test('re-hashing invalid stage data cannot bypass import validation', async () => {
  const journal = ledger();
  await journal.measure(action, () => {});
  journal.stage(receipt);
  const events = journal.events();
  const bad = events.at(-1); bad.phase = 'recovery';
  const { hash: ignored, ...body } = bad;
  bad.hash = digest(body);
  assert.throws(() => verifyLedger(events), /same-phase/);
});

test('decision evidence retains only immutable packet and precondition hashes', async () => {
  const journal = ledger();
  const decision = { packet_sha256: hash, preconditions_sha256: hash };
  await assert.rejects(journal.measure({ ...action, decision: { ...decision, payload: 'not permitted' } }, () => {}), /only packet/);
  await assert.rejects(journal.measure({ ...action, decision: { ...decision, packet_sha256: 'not-a-hash' } }, () => {}), /only packet/);
  await journal.measure({ ...action, decision }, () => {});
  journal.stage({ ...receipt, decision });
  decision.packet_sha256 = 'b'.repeat(64);
  assert.equal(journal.events()[0].decision.packet_sha256, hash);
  assert.equal(journal.events().at(-1).decision.packet_sha256, hash);
  assert.equal(verifyLedger(journal.events()).complete, true);
});

test('registration is a first-event immutable part of the reviewer-bound journal', async () => {
  const journal = ledger();
  journal.registration({ comparisonIdentity: hash, preregistrationHash: hash });
  assert.throws(() => journal.registration({ comparisonIdentity: hash, preregistrationHash: hash }), /precede/);
  await journal.measure(action, () => {});
  const events = journal.events(); events[0].comparison_identity = 'b'.repeat(64);
  assert.throws(() => verifyLedger(events), /chain mismatch/);
});
