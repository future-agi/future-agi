import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {adaptGroupingSnapshot} from './snapshot.mjs';
import {makeGroupingSnapshotFixture} from './snapshot-fixture.mjs';
import {featureDigest} from './features.mjs';
import {runCrossServiceFixture} from './cross-service.fixture.mjs';

function claim() {
  const snapshot=makeGroupingSnapshotFixture();
  const rows=adaptGroupingSnapshot(snapshot);
  const vector=Array.from({length:384},(_,index)=>index===0?1:0);
  const features=rows.flatMap(row=>['semantics','task'].map(view=>{
    const record={occurrence_id:row.id,view,source_digest:row.source_digest,
      evidence_revision:row.evidence_revision,text_digest:featureDigest(row.views[view]),
      model:'all-MiniLM-L6-v2',model_revision:null,serving_release:'smoke-release',
      dimension:384,vector,index_buckets:[]};
    return {...record,feature_digest:featureDigest(record)};
  }));
  return {attempt_id:'11111111-1111-4111-8111-111111111111',lease_token:'lease',
    policy_version:'f6-minilm/v1',
    snapshot,snapshot_digest:'sha256:'+'a'.repeat(64),
    pending_snapshots:[snapshot],pending_ids:rows.map(row=>row.id),
    registry_revision:0,candidate_digest:'sha256:'+'b'.repeat(64),
    candidate_window:{registry_revision:0,issues:[],omitted_candidates:[]},
    candidate_snapshots:[],features,constraints:[],receipt_ids:[]};
}

test('test-only bridge exercises real engine and emits a publishable receipt mapping',async()=>{
  const input={claim:claim()};
  const first=await runCrossServiceFixture(input);
  assert.equal(first.protocol,'grouping-cross-service-fixture/v1');
  assert.equal(first.raw_results.length,1);
  assert.equal(first.publish_body.commands[0].type,'create');
  assert.equal(first.publish_body.commands[0].admission.primary_receipt_id,first.receipt_ids[0]);
  assert.ok(first.checkpoint_count>=4);
  assert.ok(first.final_checkpoint.files['checkpoint.json']);
  const realIds=['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'];
  const second=await runCrossServiceFixture({...input,receipt_ids_override:realIds});
  assert.deepEqual(second.receipt_ids,realIds);
  assert.equal(second.publish_body.commands[0].admission.primary_receipt_id,realIds[0]);
  assert.deepEqual(second.raw_results.map(item=>item.result),first.raw_results.map(item=>item.result));
});

test('JSON stdin/stdout runner emits one parseable object and no source logs',()=>{
  const output=execFileSync(process.execPath,[fileURLToPath(new URL('./cross-service.fixture.mjs',import.meta.url))],
    {input:JSON.stringify({claim:claim()}),encoding:'utf8'});
  const parsed=JSON.parse(output);
  assert.equal(parsed.publish_body.commands[0].type,'create');
  assert.equal(output.trim().split('\n').length,1);
});
