import test from 'node:test';
import assert from 'node:assert/strict';
import {packInvestigation, reconciliationFitsBudget} from './pipeline.mjs';
import {F6_MINILM_POLICY} from '../policy.mjs';

test('full reconciliation evidence must fit before the model review', () => {
  assert.equal(reconciliationFitsBudget({findings:[{evidence:'short'}]},F6_MINILM_POLICY),true);
  assert.equal(reconciliationFitsBudget({findings:[{evidence:'x'.repeat(500_000)}]},F6_MINILM_POLICY),false);
});

test('discovery preserves one complete finding when a larger cohort cannot fit', () => {
  const row = id => ({id,trace_id:`trace-${id}`,kind:'failure',terminal_effect:'failed',
    outcome:'failure',control:false,evidence_revision:'revision',
    evidence:[{id:'report',digest:'digest',text:'x'.repeat(4_000)}],
    supporting_refs:['report'],refuting_refs:[],missing_evidence:[],localization:null});
  const byId = new Map(['a','b','c'].map(id => [id,row(id)]));
  const selection = {selected:['a','b','c'],controls:[],roles:{a:['seed'],b:['peer'],c:['peer']},
    unreviewed:[],missing:[]};
  const packed = packInvestigation(selection,[],byId,new Set(),
    {...F6_MINILM_POLICY,max_input_bytes:14_000});
  assert.deepEqual(packed.selection.selected,['a']);
  assert.deepEqual(packed.selection.unreviewed,['c','b']);
  assert.ok(packed.input_bytes <= 14_000);
});
