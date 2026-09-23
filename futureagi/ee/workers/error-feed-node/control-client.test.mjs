import test from 'node:test';
import assert from 'node:assert/strict';
import {createControlClient} from './control-client.mjs';

const id='11111111-1111-4111-8111-111111111111';
test('grouping routes use authenticated existing internal API without changing investigation routes',async()=>{
  const calls=[];
  const client=createControlClient({baseUrl:'http://django',token:'test-secret',fetchImpl:async(url,init)=>{
    calls.push({url:String(url),init});return Response.json({ok:true});
  }});
  for(const path of ['/notifications/','/claims/','/reports/',`/attempts/${id}/`,
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
