import test from 'node:test';
import assert from 'node:assert/strict';
import {packInvestigation, packSampledMerge, reconciliationFitsBudget} from './pipeline.mjs';
import {F6_MINILM_POLICY, SAMPLED_F6_MINILM_POLICY} from '../policy.mjs';

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


test('sampled merge shrinks whole examples to fit while covering both sources', () => {
  const ids=Array.from({length:16},(_,i)=>String(i).padStart(2,'0'));
  const rows=new Map(ids.map(id=>[id,{id,trace_id:'trace-'+id,kind:'failure',terminal_effect:'failed',
    outcome:'failure',control:false,evidence_revision:'revision',
    evidence:[{id:'report',digest:'digest',text:'x'.repeat(70_000)}],
    supporting_refs:['report'],refuting_refs:[],missing_evidence:[],localization:null}]));
  const sources=[{id:'a',members:ids.slice(0,8),prototypes:ids.slice(0,5)},
    {id:'b',members:ids.slice(8),prototypes:ids.slice(8,13)}];
  const prompt={instructions:'Sampled review',issues:sources.map(item=>({id:item.id}))};
  const selected=packSampledMerge(prompt,sources,{similarity:(a,b)=>a===b?1:0},rows,new Set(),SAMPLED_F6_MINILM_POLICY);
  assert.ok(selected.length<16);
  assert.ok(reconciliationFitsBudget(prompt,SAMPLED_F6_MINILM_POLICY));
  assert.ok(prompt.issues.every(item=>item.reviewed_member_ids.length>=2));
  assert.ok(prompt.findings.every(item=>item.evidence[0].text.length===70_000));
});
