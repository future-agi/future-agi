import test from 'node:test';
import assert from 'node:assert/strict';
import {processFeatureClaim, processGroupingClaim, engineInput} from './coordinator.mjs';
import {makeGroupingSnapshotFixture} from './snapshot-fixture.mjs';
import {adaptGroupingSnapshot} from './snapshot.mjs';
import {FEATURE_VERSION, featureDigest} from './features.mjs';
import {createGroupingInvestigator} from './gateway.mjs';

const id='11111111-1111-4111-8111-111111111111';
const model={name:'all-MiniLM-L6-v2',dimension:384,servingRelease:'test'};
function claim(){
  const snapshot=makeGroupingSnapshotFixture();
  return {attempt_id:id,lease_token:'lease',policy_version:'f6-minilm/v1',snapshot,snapshot_digest:'cohort-digest',
    candidate_digest:'sha256:'+'a'.repeat(64),
    pending_snapshots:[snapshot],pending_ids:adaptGroupingSnapshot(snapshot).map(row=>row.id),
    registry_revision:3,features:[],candidate_window:{issues:[],omitted_candidates:[],registry_revision:3},
    receipt_ids:['prior-receipt']};
}

test('feature claim uses existing serving response and persists LSH buckets',async()=>{
  let completion;
  await processFeatureClaim({...claim(),feature_attempt_id:id,policy_version:FEATURE_VERSION},{model,
    embedBatch:async texts=>({vectors:texts.map(()=>Array.from({length:384},(_,i)=>i===0?1:0))}),
    control:async(path,body)=>{completion={path,body};return {status:'ready'};}});
  assert.equal(completion.body.status,'ready');
  assert.equal(completion.body.features.length,2);
  assert.equal(completion.body.features[0].index_buckets.length,8);
  assert.equal(completion.body.features[0].model_revision,null);
});

test('pending membership mismatch is rejected before algorithm work',()=>{
  assert.throws(()=>engineInput({...claim(),pending_ids:[]}),/membership mismatch/);
});

test('grouping claim binds candidate revision and exact embedding representation',()=>{
  const base=claim();
  const [row]=adaptGroupingSnapshot(base.snapshot);
  const record={occurrence_id:row.id,view:'semantics',source_digest:row.source_digest,
    evidence_revision:row.evidence_revision,text_digest:featureDigest(row.views.semantics),
    model:model.name,model_revision:null,serving_release:model.servingRelease,
    dimension:model.dimension,vector:Array.from({length:384},(_,index)=>index===0?1:0),
    index_buckets:[]};
  base.features=[{...record,feature_digest:featureDigest(record)}];
  assert.deepEqual(engineInput(base,model).features.rows[row.id].views.semantics.vector,record.vector);
  assert.throws(()=>engineInput({...base,registry_revision:4},model),/registry revision mismatch/);
  assert.throws(()=>engineInput({...base,features:[{...base.features[0],serving_release:'other'}]},model),
    /representation mismatch/);
  assert.throws(()=>engineInput({...base,features:[{...base.features[0],vector:Array(384).fill(1)}]},model),
    /representation mismatch/);
  assert.throws(()=>engineInput({...base,features:[{...base.features[0],model:'other-model'}]},model),
    /representation mismatch/);
});

test('publication binds cohort digest, durable receipts and all F6 checkpoint files',async()=>{
  const requests=[];
  const result=await processGroupingClaim(claim(),{
    control:async(path,body)=>{requests.push({path,body});return path.endsWith('checkpoint/')
      ? {checkpoint_revision:body.expected_revision+1}:{published:true};},
    createInvestigator:async()=>({investigate:()=>{},receiptIds:()=>['new-receipt']}),
    engine:async({store})=>{
      for(const name of ['checkpoint.json','predictions.json','registry.json','prediction-receipt.json']){
        await store.save(name,{test:true});assert.deepEqual(await store.read(name),{test:true});
      }
      return {status:'complete',commands:[],dispositions:[{state:'deferred',occurrence_id:'a',reason:'unknown'}]};
    },
  });
  assert.deepEqual(result,{published:true});
  const publication=requests.at(-1).body;
  assert.equal(publication.snapshot_digest,'cohort-digest');
  assert.deepEqual(publication.receipt_ids,['prior-receipt','new-receipt']);
  assert.deepEqual(publication.commands,[{type:'defer',occurrence_ids:['a'],reason:'unknown'}]);
});

test('paused algorithm cannot publish partial assignments',async()=>{
  let published=false;
  await assert.rejects(()=>processGroupingClaim(claim(),{
    control:async()=>{published=true;},
    createInvestigator:async()=>({investigate:()=>{},receiptIds:()=>[]}),
    engine:async()=>({status:'paused'}),
  }),/no Feed publication/);
  assert.equal(published,false);
});

test('model-backed commands without a known per-command receipt never publish',async()=>{
  let published=false;
  await assert.rejects(()=>processGroupingClaim(claim(),{
    control:async()=>{published=true;return {published:true};},
    createInvestigator:async()=>({investigate:()=>{},receiptIds:()=>[]}),
    engine:async()=>({status:'complete',commands:[{type:'create',temporary_id:'new-issue',
      occurrence_ids:['finding'],citations:[]}],dispositions:[]}),
  }),/Missing durable grouping admission receipt/);
  assert.equal(published,false);
});

test('oversized checkpoint fails before write and cannot publish',async()=>{
  const writes=[];
  await assert.rejects(()=>processGroupingClaim(claim(),{
    control:async(path)=>{writes.push(path);return {checkpoint_revision:1};},
    createInvestigator:async()=>({investigate:()=>{},receiptIds:()=>[]}),
    engine:async({store})=>{
      await store.save('checkpoint.json',{large_source:'x'.repeat(2*1024*1024)});
      return {status:'complete',commands:[],dispositions:[]};
    },
  }),/checkpoint exceeds bound/);
  assert.deepEqual(writes,[]);
});

test('after a failed checkpoint, a settled receipt resumes without another paid call',async()=>{
  const work=claim();
  const settlement=new Map();
  let paidCalls=0,published=0,failCheckpoint=true;
  const control=async(path,payload)=>{
    if(path.endsWith('reserve/')){
      const old=settlement.get(payload.request_key);
      return old?{created:false,receipt_id:'receipt-1',status:'settled',
        result:old,request_digest:payload.request_digest}
        :{created:true,receipt_id:'receipt-1',status:'reserved',
          request_digest:payload.request_digest};
    }
    if(path.endsWith('settle/')){
      settlement.set(payload.request_key,payload.result);
      return {status:'settled'};
    }
    if(path.endsWith('checkpoint/'))return {checkpoint_revision:payload.expected_revision+1};
    if(path.endsWith('publish/')){published++;return {published:true};}
    throw new Error('Unexpected grouping control path');
  };
  const createProvider=()=>{
    const calls=[];
    return {accounting:()=>({calls}),provider:{generate:async()=>{
      paidCalls++;
      calls.push({cost_microusd:100,routed_model:'google/gemini-3.8-flash'});
      return {content:'{"groups":[]}',raw:{choices:[{finish_reason:'stop'}]}};
    }}};
  };
  const options={control,model,reserveUsd:1,
    gatewayConfig:{model:'google/gemini-3.8-flash'},
    createInvestigator:args=>createGroupingInvestigator({...args,createProvider,
      countRequest:async()=>({input_tokens:100,request_bytes:1000}),onDiagnostic:()=>{}}),
    engine:async({investigate,store})=>{
      await investigate({same:'proposal'},{type:'object'});
      await store.save('checkpoint.json',failCheckpoint
        ? {large_source:'x'.repeat(2*1024*1024)}:{phase:'complete'});
      return {status:'complete',commands:[],dispositions:[{state:'deferred',
        occurrence_id:work.pending_ids[0],reason:'No supported mechanism'}]};
    }};
  await assert.rejects(()=>processGroupingClaim(work,options),/checkpoint exceeds bound/);
  assert.equal(paidCalls,1);
  assert.equal(published,0);
  failCheckpoint=false;
  assert.deepEqual(await processGroupingClaim(work,options),{published:true});
  assert.equal(paidCalls,1);
  assert.equal(published,1);
});
