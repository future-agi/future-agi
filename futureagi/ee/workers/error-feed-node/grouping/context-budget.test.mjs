import test from 'node:test';
import assert from 'node:assert/strict';
import {digest} from './f6/common.mjs';
import {F6_MINILM_POLICY} from './policy.mjs';
import {migrateContextBudget} from './context-budget.mjs';

for (const oldLimit of [60000,240000]) test(`context migration from ${oldLimit} preserves receipts and rejects other input changes`, () => {
  const oldPolicy = {...F6_MINILM_POLICY, max_input_bytes:oldLimit};
  delete oldPolicy.digest;
  oldPolicy.digest = digest(oldPolicy);
  const inputs = {rows:[{id:'a'}], policy:F6_MINILM_POLICY};
  const state = {binding:digest({...inputs,policy:oldPolicy}), receipts:['receipt'], attempts:{a:2}};
  const result = migrateContextBudget(state, inputs);
  assert.equal(result.binding, digest(inputs));
  assert.deepEqual(result.receipts, state.receipts);
  assert.deepEqual(result.attempts, state.attempts);
  assert.equal(result.resource_transitions.length, 1);
  assert.equal(migrateContextBudget(result,inputs),result);
  assert.equal(migrateContextBudget(state,{...inputs,rows:[{id:'other'}]}),state);
});
