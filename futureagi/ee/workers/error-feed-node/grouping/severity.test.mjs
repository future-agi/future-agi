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
test('publishes only a durable receipt, not an unchecked worker assessment',async()=>{
  const result={severity:'high'};
  const investigate=async()=>result;
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
