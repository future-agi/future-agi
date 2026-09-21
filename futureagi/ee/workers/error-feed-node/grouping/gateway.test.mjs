import test from 'node:test';
import assert from 'node:assert/strict';
import {createGroupingInvestigator} from './gateway.mjs';

const claim={attempt_id:'11111111-1111-4111-8111-111111111111',lease_token:'lease',
  snapshot:{snapshot_digest:'sha256:source'},policy_version:'f6-minilm/v1',registry_revision:0,
  candidate_digest:'sha256:'+'a'.repeat(64)};
const config={model:'google/gemini-3.8-flash',baseUrl:'http://gateway/v1',apiKey:'fixture'};
function fixture({prior=false,unknown=false,invalid=false,unverifiedModel=false,badUsage=false}={}) {
  const events=[],calls=[];let body;
  const control=async(path,payload)=>{
    events.push({path,payload});
    if(path.endsWith('reserve/'))return{created:!prior,receipt_id:'receipt',status:'reserved',request_digest:payload.request_digest};
    return{status:'settled'};
  };
  const createProvider=options=>({accounting:()=>({calls}),provider:{generate:async()=>{
    const response=await options.fetchImpl('http://gateway/v1/chat/completions',{body:'{}'});
    calls.push({cost_microusd:unknown?null:123,usage:badUsage?{prompt_tokens:10,total_tokens:5}:{prompt_tokens:10,total_tokens:15},
      routed_model:unverifiedModel?null:config.model});
    return {content:invalid?'invalid':JSON.stringify({groups:[]}),raw:{choices:[{finish_reason:'stop'}]}};
  }}});
  return{events,calls,control,createProvider,fetchImpl:async(_url,init)=>{
    body=JSON.parse(init.body);return new Response('{}');
  },body:()=>body};
}

test('reserves before inference, enforces F6 wire settings, persists receipt before returning',async()=>{
  const f=fixture();const gateway=await createGroupingInvestigator({claim,config,reserveUsd:1,...f});
  const result=await gateway.investigate({question:'compare'}, {type:'object'});
  assert.deepEqual(result,{groups:[]});
  assert.equal(gateway.investigate.receiptFor(result),'receipt');
  assert.ok(f.events[0].path.endsWith('reserve/'));
  assert.ok(f.events[1].path.endsWith('settle/'));
  assert.equal(f.body().max_completion_tokens,8192);
  assert.equal(f.body().reasoning_effort,'low');
  assert.equal(f.body().response_format.type,'json_schema');
  assert.equal(f.events[1].payload.cost_usd,'0.000123000');
  assert.deepEqual(gateway.receiptIds(),['receipt']);
});

test('citation repair intent is reserved and bound into its request identity',async()=>{
  const f=fixture();const gateway=await createGroupingInvestigator({claim,config,reserveUsd:1,...f});
  const intent={primary_receipt_id:'receipt',group_index:0,
    missing_own_report_ids:['finding-1']};
  const repair=await gateway.investigate({fixed_group:'proposal'},{type:'object'},[],
    {repairIntent:intent});
  assert.equal(gateway.investigate.receiptFor(repair),'receipt');
  assert.deepEqual(f.events[0].payload.repair_intent,intent);
  const repairRequest=f.events[0].payload.request_digest;
  await gateway.investigate({fixed_group:'proposal'},{type:'object'});
  assert.notEqual(f.events[2].payload.request_digest,repairRequest);
});

test('prior reserved request is never automatically sent again',async()=>{
  const f=fixture({prior:true});const gateway=await createGroupingInvestigator({claim,config,reserveUsd:1,...f});
  await assert.rejects(()=>gateway.investigate({},{}),/refusing automatic resend/);
  assert.equal(f.calls.length,0);
});

test('unknown cost retains reservation and the received response, never invents zero',async()=>{
  const f=fixture({unknown:true});const gateway=await createGroupingInvestigator({claim,config,reserveUsd:1,...f});
  await gateway.investigate({},{});
  assert.equal(f.events[1].payload.status,'unknown');
  assert.equal(f.events[1].payload.cost_usd,null);
  assert.deepEqual(f.events[1].payload.result,{groups:[]});
});

test('missing routed-model evidence is settled as unknown, not echoed from request',async()=>{
  const f=fixture({unverifiedModel:true});
  const gateway=await createGroupingInvestigator({claim,config,reserveUsd:1,...f});
  await gateway.investigate({},{});
  assert.equal(f.events[1].payload.model_used,null);
});

test('malformed token accounting cannot discard a known charge or received result',async()=>{
  const f=fixture({badUsage:true});
  const gateway=await createGroupingInvestigator({claim,config,reserveUsd:1,...f});
  await gateway.investigate({},{});
  assert.equal(f.events[1].payload.output_tokens,null);
  assert.equal(f.events[1].payload.cost_usd,'0.000123000');
  assert.deepEqual(f.events[1].payload.result,{groups:[]});
});

test('invalid output still settles actual failed-call cost and does not become a proposal',async()=>{
  const f=fixture({invalid:true});const gateway=await createGroupingInvestigator({claim,config,reserveUsd:1,...f});
  await assert.rejects(()=>gateway.investigate({},{}),/invalid output/);
  assert.equal(f.events[1].payload.result,null);
  assert.equal(f.events[1].payload.cost_usd,'0.000123000');
});

test('settled cached response is reused without creating a provider call',async()=>{
  const f=fixture();f.control=async(_path,payload)=>({created:false,receipt_id:'old',
    status:'settled',request_digest:payload.request_digest,result:{groups:[]}});
  const gateway=await createGroupingInvestigator({claim,config,reserveUsd:1,...f});
  const cached=await gateway.investigate({},{});
  assert.deepEqual(cached,{groups:[]});
  assert.equal(gateway.investigate.receiptFor(cached),'old');
  assert.equal(f.calls.length,0);
});

test('a settled model result is reused after a later checkpoint write fails',async()=>{
  let savedResult=null, paidCalls=0;
  const control=async(path,payload)=>{
    if(path.endsWith('reserve/'))return savedResult
      ? {created:false,receipt_id:'durable-receipt',status:'settled',
        request_digest:payload.request_digest,result:savedResult}
      : {created:true,receipt_id:'durable-receipt',status:'reserved',
        request_digest:payload.request_digest};
    if(path.endsWith('settle/')){savedResult=payload.result;return {status:'settled'};}
    throw new Error('Unexpected control call');
  };
  const createProvider=()=>({accounting:()=>({calls:[{cost_microusd:300,
    routed_model:config.model}]}),provider:{generate:async()=>{
    paidCalls++;
    return {content:JSON.stringify({groups:[]}),raw:{choices:[{finish_reason:'stop'}]}};
  }}});
  const options={claim,config,reserveUsd:1,control,createProvider};
  const first=await createGroupingInvestigator(options);
  assert.deepEqual(await first.investigate({same:'prompt'},{type:'object'}),{groups:[]});
  await assert.rejects(async()=>{throw new Error('Grouping checkpoint exceeds bound');},
    /checkpoint exceeds bound/);
  const resumed=await createGroupingInvestigator(options);
  assert.deepEqual(await resumed.investigate({same:'prompt'},{type:'object'}),{groups:[]});
  assert.equal(paidCalls,1);
  assert.deepEqual(resumed.receiptIds(),['durable-receipt']);
});
