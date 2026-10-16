// Explicit opt-in: exports synthetic worker traces to the configured collector.
// Inference, evidence storage and Django are fixtures; no production jobs are claimed.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {randomUUID} from 'node:crypto';
import {mkdtemp, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {startObservability, stopObservability} from './observability.mjs';
import {investigateTrace, canonicalDigest} from './investigation.mjs';
import {storeEvidence} from './evidence-store.mjs';
import {processGroupingClaim} from './grouping/coordinator.mjs';
import {createGroupingInvestigator} from './grouping/gateway.mjs';
import {makeGroupingSnapshotFixture} from './grouping/snapshot-fixture.mjs';
import {adaptGroupingSnapshot} from './grouping/snapshot.mjs';

assert.equal(process.env.OMEGA_OBSERVABILITY_ENABLED, 'true', 'Explicit observability opt-in required');
assert.ok(process.env.FI_BASE_URL && process.env.FI_PROJECT_NAME, 'Explicit destination and project required');
const destination = new URL(process.env.FI_BASE_URL);
assert.equal(destination.protocol, 'https:');
assert.equal(destination.pathname, '/tracer/v1/traces');
assert.ok(!destination.username && !destination.password && !destination.search && !destination.hash);
const exports = [], spans = [];
// Local relay records only delivery statuses and span identities. Secrets are never logged.
const relay = createServer(async (request, response) => {
  try {
    if (request.url !== '/tracer/v1/traces' || request.method !== 'POST') {
      response.writeHead(404); response.end(); return;
    }
    const chunks = [];
    let size = 0;
    for await (const chunk of request) {
      size += chunk.length;
      if (size > 1024 * 1024) throw new Error('Smoke export exceeds bound');
      chunks.push(chunk);
    }
    const body = Buffer.concat(chunks);
    const payload = JSON.parse(body.toString());
    for (const resource of payload.resourceSpans ?? []) {
      for (const scope of resource.scopeSpans ?? []) {
        for (const span of scope.spans ?? []) {
          const attributes = Object.fromEntries(span.attributes.map(item => [item.key, item.value.stringValue]));
          spans.push({name:span.name,trace_id:span.traceId,span_id:span.spanId,
            parent_span_id:span.parentSpanId,user_id:attributes['user.id'],session_id:attributes['session.id']});
        }
      }
    }
    const upstream = await fetch(destination, {method:'POST', redirect:'error',
      headers:{'Content-Type':request.headers['content-type'],
        'x-api-key':request.headers['x-api-key'],'x-secret-key':request.headers['x-secret-key']},
      body,signal:AbortSignal.timeout(15000)});
    const text = await upstream.text();
    let rejectedSpans = 0;
    try {
      const parsed = JSON.parse(text);
      rejectedSpans = Number(parsed.partialSuccess?.rejectedSpans ?? parsed.partial_success?.rejected_spans ?? 0);
    } catch { /* A success response may be empty. */ }
    exports.push({status:upstream.status,rejected_spans:rejectedSpans});
    response.writeHead(upstream.status, {'content-type':'application/json'});
    // Do not surface response bodies, which may contain account details.
    response.end(upstream.ok ? '{}' : '{"error":"collector_rejected_export"}');
  } catch {
    exports.push({status:0,error:'collector_transport_failed'});
    response.writeHead(502); response.end('{"error":"collector_transport_failed"}');
  }
});
await new Promise(resolve => relay.listen(0,'127.0.0.1',resolve));
const scratch = await mkdtemp(join(tmpdir(),'error-feed-observability-smoke-'));
const snapshot = makeGroupingSnapshotFixture();
const organizationId = snapshot.report.organization_id;
const projectId = snapshot.report.project_id;
let report, grouping;
try {
  await startObservability({...process.env,FI_BASE_URL:`http://127.0.0.1:${relay.address().port}/tracer/v1/traces`});
  const claim = {organization_id:organizationId,workspace_id:null,project_id:projectId,trace_id:randomUUID(),
    job_id:randomUUID(),attempt_id:randomUUID(),generation:1,feature_enabled:true,
    engine_version:'omega-observability-smoke',contract_version:'omega-investigation/v1',
    read_cutoff:new Date().toISOString(),memory:{snapshot_id:'empty',digest:canonicalDigest([]),entries:[]},
    limits:{deadline_seconds:60,max_model_calls:12,max_children:2,max_input_tokens_total:100000,
      max_output_tokens_total:8000,max_evidence_bytes:65536,max_tool_result_bytes:4096}};
  const row = {id:'smoke-span',project_id:projectId,trace_id:claim.trace_id,
    input:'Synthetic observability test: refund 10',output:'Synthetic refund completed: 10'};
  const raw = JSON.stringify(row);
  const evidenceId = `${row.id}:0:${Buffer.byteLength(raw)}`;
  const assessment = {outcome:'success',findings:[],requirement_checks:[{
    requirement_id:'refund',requirement:'Refund 10',status:'satisfied',evidence_ids:[evidenceId]}]};
  const fixtureReply = content => new Response(JSON.stringify({id:randomUUID(),
    choices:[{message:content,finish_reason:'stop'}],usage:{prompt_tokens:100,completion_tokens:20,total_tokens:120}}),
    {headers:{'content-type':'application/json','x-agentcc-cost':'0.000000',
      'x-agentcc-model-used':'synthetic-smoke-model','x-request-id':randomUUID()}});
  report = await investigateTrace(claim,{scratchRoot:scratch,
    fetchEvidence:async(c,path)=>storeEvidence([Buffer.from(raw+'\n')],path,c),
    gatewayConfig:{baseUrl:'http://fixture/v1',model:'synthetic-smoke-model',apiKey:'fixture',
      fetchImpl:async(_url,init)=>{
        const request=JSON.parse(init.body);
        const verifier=request.messages.some(item=>item.role==='system' && item.content.includes('Independently check'));
        if (!request.messages.some(item=>item.role==='tool')) {
          return fixtureReply({role:'assistant',content:'',tool_calls:[{id:randomUUID(),type:'function',
            function:{name:'read_span',arguments:JSON.stringify({span_id:row.id,offset:0,length:4096})}}]});
        }
        return fixtureReply({role:'assistant',content:JSON.stringify(verifier?assessment:
          {action:'finish',question:'',child_instructions:'',assessment})});
      }}});
  assert.equal(report.execution_status,'completed');
  const groupingClaim={organization_id:organizationId,project_id:projectId,attempt_id:randomUUID(),lease_token:'synthetic',
    policy_version:'f6-minilm/v1',snapshot,snapshot_digest:snapshot.snapshot_digest,
    candidate_digest:'sha256:'+'a'.repeat(64),pending_snapshots:[snapshot],
    pending_ids:adaptGroupingSnapshot(snapshot).map(row=>row.id),registry_revision:0,features:[],
    candidate_window:{issues:[],omitted_candidates:[],registry_revision:0},receipt_ids:[]};
  const control=async(path,body)=>{
    if(path.endsWith('/reserve/')) return {created:true,status:'reserved',receipt_id:randomUUID(),request_digest:body.request_digest};
    if(path.endsWith('/settle/')) return {status:'settled'};
    if(path.endsWith('/publish/')) return {published:true};
    throw new Error('Unexpected fixture control call');
  };
  grouping=await processGroupingClaim(groupingClaim,{control,reserveUsd:0.01,
    gatewayConfig:{model:'google/gemini-3.8-flash',baseUrl:'http://fixture/v1',apiKey:'fixture'},
    createInvestigator:options=>createGroupingInvestigator({...options,onDiagnostic:()=>{},
      countRequest:async()=>({input_tokens:100,request_bytes:1000}),
      fetchImpl:async()=>fixtureReply({role:'assistant',content:JSON.stringify({groups:[],deferred:[]})})}),
    engine:async({investigate})=>{
      await investigate({task:'Synthetic observability smoke'}, {type:'object',properties:{groups:{type:'array'},deferred:{type:'array'}}});
      return {status:'complete',commands:[],dispositions:[]};
    }});
  assert.equal(grouping.published,true);
} finally {
  await stopObservability();
  await new Promise(resolve=>relay.close(resolve));
  await rm(scratch,{recursive:true,force:true});
}
const receipt={project:process.env.FI_PROJECT_NAME,endpoint:destination.href,
  synthetic:true,inference:'scripted',control_plane:'in-memory fixture',organization_id:organizationId,
  investigation_status:report?.execution_status,grouping_published:grouping?.published,exports,spans};
if(process.env.OMEGA_SMOKE_RECEIPT) await writeFile(process.env.OMEGA_SMOKE_RECEIPT,JSON.stringify(receipt,null,2)+'\n',{mode:0o600});
console.log(JSON.stringify({project:receipt.project,endpoint:receipt.endpoint,span_count:spans.length,
  trace_ids:[...new Set(spans.map(span=>span.trace_id))],exports},null,2));
assert.ok(exports.length>0 && exports.every(item=>item.status>=200 && item.status<300 && item.rejected_spans===0),
  'Collector did not accept all exports; inspect status-only receipt');
assert.ok(spans.some(span=>span.name==='error_feed.investigation'));
assert.ok(spans.some(span=>span.name==='error_feed.grouping'));
assert.ok(spans.every(span=>span.user_id===organizationId),'All spans must identify the source organization');
