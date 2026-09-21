import test from 'node:test';
import assert from 'node:assert/strict';
import {measureRequest, requestLimits, reservationUsd} from './request-limits.mjs';

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
    [Response.json({token_count:100}),/Invalid/],[new Response('',{status:404}),/unavailable/]]) {
    await assert.rejects(measureRequest({body,config,fetchImpl:async()=>response}),pattern);
  }
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
