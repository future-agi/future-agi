import test from 'node:test';
import assert from 'node:assert/strict';
import { ActionLedger, summarizeLedger } from '../../lib/skills-r3/ledger.mjs';
import { MeasuredOperator } from '../../lib/skills-r3/measured-operator.mjs';

function setup() {
  const ledger = new ActionLedger({ runId: 'operator', job: 'J1', mode: 'selftest' });
  return { ledger, operator: new MeasuredOperator({ ledger, actor: 'engineer', step: 'inventory', phase: 'cold' }) };
}
test('browser primitives count individually without storing field values or copied URLs', async () => {
  const { ledger, operator } = setup(); const calls = [];
  const locator = { click: async () => calls.push('click'), fill: async (value) => calls.push(value) };
  await operator.click(locator, 'open-library');
  await operator.fill(locator, 'synthetic-private-text', 'description');
  await operator.transition('editor', async () => {});
  await operator.handoff('reviewer', async () => {});
  await operator.reconcile('version', async () => {});
  await operator.review('packet', ['context-1', 'context-2'], ['context-2'], async () => {});
  assert.deepEqual(calls, ['click', 'synthetic-private-text']);
  assert.deepEqual(summarizeLedger(ledger.events()).counts, { A: 3, S: 1, H: 1, R: 1 });
  assert.ok(!JSON.stringify(ledger.events()).includes('synthetic-private-text'));
});

test('cloud CLI records each invocation, failed command and output hash without secret-bearing args/output', async () => {
  const { ledger, operator } = setup();
  const result = await operator.command('synthetic-cli', process.execPath, ['-e', 'process.stdout.write("synthetic-private-output")']);
  assert.equal(result.stdout, 'synthetic-private-output');
  await assert.rejects(operator.command('synthetic-cli', process.execPath, ['-e', 'process.exit(2)']));
  const summary = summarizeLedger(ledger.events());
  assert.equal(summary.counts.A, 2); assert.equal(summary.failed_actions, 1);
  assert.ok(!JSON.stringify(ledger.events()).includes('synthetic-private-output'));
  assert.equal(ledger.events().filter((e) => e.type === 'observation').length, 1);
});

test('cancelled operators cannot start more user actions', async () => {
  const { ledger } = setup(); const controller = new AbortController(); controller.abort();
  const operator = new MeasuredOperator({ ledger, actor: 'engineer', step: 'inventory', phase: 'cold', signal: controller.signal });
  assert.throws(() => operator.click({ click: () => assert.fail('must not execute') }, 'button'), /cancelled/);
  assert.equal(ledger.events().length, 0);
});


test('human decision adapters preserve packet and precondition bindings', async () => {
  const { ledger, operator } = setup();
  const decision = { packet_sha256: 'a'.repeat(64), preconditions_sha256: 'b'.repeat(64) };
  await operator.review('packet', ['item'], [], () => {}, decision);
  await operator.approve('packet', ['item'], () => {}, decision);
  await operator.recoveryDecision('packet', () => {}, decision);
  const starts = ledger.events().filter((event) => event.type === 'start');
  assert.equal(starts.length, 3);
  assert.ok(starts.every((event) => JSON.stringify(event.decision) === JSON.stringify(decision)));
});
