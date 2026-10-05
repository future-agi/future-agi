import test from 'node:test';
import assert from 'node:assert/strict';
import {createControlClient, controlFailureDetails} from './control-client.mjs';

const id='11111111-1111-4111-8111-111111111111';
test('control routes include simulation evidence without widening unrelated investigation operations',async()=>{
  const calls=[];
  const client=createControlClient({baseUrl:'http://django',token:'test-secret',fetchImpl:async(url,init)=>{
    calls.push({url:String(url),init});return Response.json({ok:true});
  }});
  for(const path of ['/notifications/','/claims/','/reports/',`/attempts/${id}/`,
    `/attempts/${id}/simulation-evidence/`,
    '/grouping/feature-claims/','/grouping/claims/',`/grouping/feature-attempts/${id}/complete/`,
    '/grouping/severity/claims/',`/grouping/severity/attempts/${id}/`,
    ...['reserve','settle','publish'].map(part=>`/grouping/severity/attempts/${id}/${part}/`),
    ...['checkpoint','reserve','settle','publish'].map(part=>`/grouping/attempts/${id}/${part}/`)]) {
    assert.deepEqual(await client(path,{}),{ok:true});
  }
  assert.ok(calls.every(call=>call.url.startsWith('http://django/tracer/internal/error-feed-v2/')));
  assert.ok(calls.every(call=>call.init.headers.Authorization==='Bearer test-secret'&&call.init.redirect==='error'));
  const before=calls.length;
  await assert.rejects(()=>client('/grouping/../reports/',{}),/Unsupported/);
  await assert.rejects(()=>client('/grouping/claims/',{value:'x'.repeat(8*1024*1024)}),/exceeded/);
  assert.equal(calls.length,before);
});

test('control errors do not expose backend response bodies',async()=>{
  const client=createControlClient({baseUrl:'http://django',token:'test',
    fetchImpl:async()=>new Response('secret data',{status:409})});
  await assert.rejects(()=>client('/grouping/claims/',{}),error=>error.status===409&&!error.message.includes('secret'));
});

test('conflicts retain only known backend reasons and request diagnostics',async()=>{
  const path=`/grouping/attempts/${id}/publish/`;
  const reason='attach target is stale or not offered';
  const client=createControlClient({baseUrl:'http://django',token:'private-token',
    fetchImpl:async()=>Response.json({code:'grouping_conflict',detail:reason,secret:'private-response'}, {status:409})});
  await assert.rejects(()=>client(path,{lease_token:'private-lease'}),error=>{
    const fields=controlFailureDetails(error);
    assert.equal(fields.backend_reason,reason);
    assert.equal(fields.backend_code,'grouping_conflict');
    assert.equal(fields.http_status,409);
    assert.equal(fields.control_path,path);
    assert.equal(fields.http_method,'POST');
    assert.equal(fields.failure_code,'control_http_error');
    assert.ok(fields.duration_ms>=0);
    assert.equal(fields.request_bytes,Buffer.byteLength(JSON.stringify({lease_token:'private-lease'})));
    assert.doesNotMatch(JSON.stringify(fields),/private/);
    assert.doesNotMatch(error.message,/attach/);
    return true;
  });
});

test('unknown, malformed, oversized, and non-conflict bodies remain private',async()=>{
  for (const [body,status] of [
    [JSON.stringify({code:'grouping_conflict',detail:'private-customer-data'}),409],
    ['private-html',409],
    ['x'.repeat(16385),409],
    [JSON.stringify({code:'grouping_conflict',detail:'attach target is stale or not offered'}),500],
  ]) {
    const client=createControlClient({baseUrl:'http://django',token:'private-token',
      fetchImpl:async()=>new Response(body,{status})});
    await assert.rejects(()=>client('/grouping/claims/',{}),error=>{
      assert.equal(error.status,status);
      assert.equal(controlFailureDetails(error).backend_reason,undefined);
      assert.doesNotMatch(JSON.stringify(controlFailureDetails(error)),/private/);
      return true;
    });
  }
  assert.deepEqual(controlFailureDetails(new Error('private-provider-error')),{});
});

test('network and cancelled requests retain safe diagnostics',async()=>{
  const stop=new AbortController(); stop.abort();
  for (const signal of [undefined,stop.signal]) {
    const failure=new Error('private-network-error');
    const client=createControlClient({baseUrl:'http://django',token:'private-token',
      fetchImpl:async()=>{throw failure;}});
    await assert.rejects(()=>client('/grouping/claims/',{},{signal}),error=>{
      assert.equal(error,failure);
      const fields=controlFailureDetails(error);
      assert.equal(fields.failure_code,signal?'control_cancelled':'control_network_error');
      assert.doesNotMatch(JSON.stringify(fields),/private/);
      return true;
    });
  }
});
