import test from 'node:test';
import assert from 'node:assert/strict';
import {reconciliationFitsBudget} from './pipeline.mjs';
import {F6_MINILM_POLICY} from '../policy.mjs';

test('full reconciliation evidence must fit before the model review', () => {
  assert.equal(reconciliationFitsBudget({findings:[{evidence:'short'}]},F6_MINILM_POLICY),true);
  assert.equal(reconciliationFitsBudget({findings:[{evidence:'x'.repeat(500_000)}]},F6_MINILM_POLICY),false);
});
