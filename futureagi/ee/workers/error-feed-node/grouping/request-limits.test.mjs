import test from 'node:test';
import assert from 'node:assert/strict';
import {MAX_EVIDENCE_BYTES, measureRequest, requestLimits, reservationUsd} from './request-limits.mjs';
import {createGroupingInvestigator} from './gateway.mjs';
import {groupingFailureCode} from './coordinator.mjs';

const config={baseUrl:'http://gateway/v1',model:'vertex_ai/gemini-3.8-flash',apiKey:'fixture'};
const body={messages:[{role:'system',content:'rules'},{role:'user',content:'evidence'}],
  response_format:{json_schema:{schema:{type:'object'}}},max_completion_tokens:8192};
test('native token count includes system, evidence and schema through the same model route',async()=>{
  const result=await measureRequest({body,config,fetchImpl:async(url,init)=>{
    assert.equal(url.pathname,'/v1beta/models/vertex_ai/gemini-3.8-flash:countTokens');
    const request=JSON.parse(init.body);
    assert.equal(request.systemInstruction.parts[0].text,'rules');
    assert.equal(request.contents[0].parts[0].text,'evidence');
    assert.deepEqual(request.generationConfig.responseSchema,{type:'object'});
    return Response.json({totalTokens:1_000_000});
  }});
  assert.equal(result.input_tokens,1_000_000);
  assert.equal(result.request_bytes,Buffer.byteLength(JSON.stringify(body)));
});
test('over-limit tokens, missing counts and unavailable counting fail closed',async()=>{
  for(const [response,pattern] of [[Response.json({totalTokens:1_000_001}),/token limit/],
    [Response.json({totalTokens:-1}),/Invalid/],
    [Response.json({token_count:100}),/Invalid/],[new Response('',{status:503}),/unavailable/]]) {
    await assert.rejects(measureRequest({body,config,fetchImpl:async()=>response}),pattern);
  }
});
test('gateway without native token count uses a bounded UTF-8 upper bound',async()=>{
  const result=await measureRequest({body,config,fetchImpl:async()=>new Response('',{status:404})});
  assert.equal(result.input_tokens,Buffer.byteLength(JSON.stringify(body))+8192);
  assert.equal(result.count_source,'utf8_upper_bound');
  await assert.rejects(measureRequest({body,config,
    limits:{...requestLimits({}),inputTokens:result.input_tokens-1},
    fetchImpl:async()=>new Response('',{status:404})}),/unavailable/);
});
test('a packed evidence prompt leaves room for gateway JSON escaping without native token count',async()=>{
  const prompt=JSON.stringify({evidence:'x'.repeat(MAX_EVIDENCE_BYTES-100)});
  assert.ok(Buffer.byteLength(prompt)<=MAX_EVIDENCE_BYTES);
  const packed={...body,messages:[body.messages[0],{role:'user',content:prompt}]};
  const measured=await measureRequest({body:packed,config,
    fetchImpl:async()=>new Response('',{status:404})});
  assert.ok(measured.input_tokens<requestLimits({}).inputTokens);
});
test('transport bound rejects before any remote call; configuration cannot exceed model ceiling',async()=>{
  await assert.rejects(measureRequest({body,config,limits:{...requestLimits({}),requestBytes:1},
    fetchImpl:async()=>{assert.fail('must not call gateway');}}),/transport limit/);
  assert.throws(()=>requestLimits({GROUPING_MAX_INPUT_TOKENS:'1048576'}),/Invalid/);
  assert.throws(()=>requestLimits({GROUPING_INPUT_USD_PER_MILLION:'0'}),/Invalid/);
});
test('reservation covers full uncached input plus maximum output, retaining the configured floor',()=>{
  assert.equal(reservationUsd(1_000_000,8192,0.1,requestLimits({})),1.56144);
  assert.equal(reservationUsd(10,8192,0.1,requestLimits({})),0.1);
});

test('transient token-count failures remain retryable before any reservation or inference',async()=>{
  const networkError=new TypeError('fetch failed');
  const timeoutError=new DOMException('Timed out','TimeoutError');
  const bodyError=new TypeError('Response connection terminated');
  const failures=[
    async()=>new Response('',{status:429}),
    async()=>new Response('',{status:503}),
    async()=>new Response(new ReadableStream({start(controller){controller.error(bodyError);}}),{status:503}),
    async()=>{throw networkError;},
    async()=>{throw timeoutError;},
    async()=>new Response(new ReadableStream({start(controller){controller.error(bodyError);}})),
  ];
  for(const fetchImpl of failures){
    let reservations=0,providerCalls=0;
    const gateway=await createGroupingInvestigator({
      claim:{attempt_id:'11111111-1111-4111-8111-111111111111',lease_token:'lease',
        candidate_digest:'sha256:'+'a'.repeat(64),policy_version:'f6-minilm/v1'},
      config,reserveUsd:0.1,fetchImpl,onDiagnostic:()=>{},
      control:async()=>{reservations++;assert.fail('must not reserve');},
      createProvider:()=>({accounting:()=>({calls:[]}),
        provider:{generate:async()=>{providerCalls++;assert.fail('must not infer');}}}),
    });
    await assert.rejects(gateway.investigate({},{}),error=>{
      assert.equal(groupingFailureCode(error),'gateway_error');
      return true;
    });
    assert.equal(reservations,0);
    assert.equal(providerCalls,0);
  }
});

test('token-count context bounds park while malformed responses and ordinary HTTP rejections stay terminal',async()=>{
  const defaults=requestLimits({});
  const cases=[
    {limits:{...defaults,requestBytes:1},code:'context_budget'},
    {fetchImpl:async()=>Response.json({totalTokens:defaults.inputTokens+1}),code:'context_budget'},
    {limits:{...defaults,inputTokens:1},fetchImpl:async()=>new Response('',{status:404}),code:'context_budget'},
    {fetchImpl:async()=>Response.json({totalTokens:-1}),code:'grouping_worker_error'},
    {fetchImpl:async()=>Response.json({token_count:100}),code:'grouping_worker_error'},
    {fetchImpl:async()=>Response.json(null),code:'grouping_worker_error'},
    {fetchImpl:async()=>new Response('not JSON'),code:'grouping_worker_error'},
    {fetchImpl:async()=>new Response('',{status:403}),code:'grouping_worker_error'},
  ];
  for(const {code,...options} of cases){
    await assert.rejects(measureRequest({body,config,
      fetchImpl:async()=>{assert.fail('must not call gateway');},...options}),error=>{
      assert.equal(groupingFailureCode(error),code);
      return true;
    });
  }
});
