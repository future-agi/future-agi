import test from 'node:test';
import assert from 'node:assert/strict';
import {severityPrompt, assessSeverity, SEVERITY_POLICY_VERSION} from './severity.mjs';

const snapshot = {policy_version:SEVERITY_POLICY_VERSION,members:[{
  occurrence_id:'a',finding:{statement:'Request failed',recovery:'recovered'},
  outcome:'success',evidence:[{ref:'receipt:1',text:'Unmodified evidence',digest:'digest'}],
}]};

test('severity rubric preserves evidence and separates recovery/frequency from harm',()=>{
  const prompt=severityPrompt(snapshot);
  assert.deepEqual(prompt.evidence,snapshot);
  assert.match(prompt.rules.join(' '),/Frequency alone never raises severity/);
  assert.match(prompt.rules.join(' '),/recovered/);
  assert.ok(prompt.rubric.insufficient_evidence);
});
test('invalid or oversized evidence fails before inference',()=>{
  assert.throws(()=>severityPrompt({...snapshot,members:[]}),/Invalid/);
  assert.throws(()=>severityPrompt({...snapshot,extra:'x'.repeat(200000)}),/Invalid/);
});
test('v2 independently classifies the corrective layer without guessing from tool presence',()=>{
  const prompt=severityPrompt(snapshot);
  assert.equal(Object.keys(prompt.fix_layer_rubric).length,7);
  assert.match(prompt.rules.join(' '),/own evidence citations/);
  assert.match(prompt.rules.join(' '),/incomplete tool output/);
  assert.deepEqual(prompt.evidence,snapshot);
});
test('legacy assessment retains its original prompt and schema',async()=>{
  const legacy={...snapshot,policy_version:'feed-severity/v1'};
  assert.equal(severityPrompt(legacy).fix_layer_rubric,undefined);
  let schema;
  const result={severity:'high'};
  const investigate=async(_prompt,value)=>{schema=value;return result;};
  investigate.receiptFor=()=> 'receipt';
  await assessSeverity({attempt_id:'old',policy_version:'feed-severity/v1',snapshot:legacy},{
    gateway:{investigate},control:async()=>{},
  });
  assert.deepEqual(schema.required,['severity','reason','citations']);
});
test('publishes only a durable receipt, not an unchecked worker assessment',async()=>{
  const result={severity:'high'};
  const investigate=async(_prompt,schema)=>{
    assert.ok(schema.required.includes('fix_layer'));
    assert.ok(schema.properties.fix_layer.properties.layer.enum.includes('insufficient_evidence'));
    return result;
  };
  investigate.receiptFor=value=>value===result?'receipt-id':null;
  const calls=[];
  await assessSeverity({attempt_id:'job',lease_token:'token',policy_version:SEVERITY_POLICY_VERSION,snapshot},{
    gateway:{investigate},control:async(path,body)=>calls.push({path,body}),
  });
  assert.deepEqual(calls,[{path:'/grouping/severity/attempts/job/publish/',body:{lease_token:'token',receipt_id:'receipt-id'}}]);
});
test('provider failure cannot publish a default as a model result',async()=>{
  let published=false;
  await assert.rejects(assessSeverity({policy_version:SEVERITY_POLICY_VERSION,snapshot},{
    gateway:{investigate:async()=>{throw new Error('provider failed');}},
    control:async()=>{published=true;},
  }),/provider failed/);
  assert.equal(published,false);
});
