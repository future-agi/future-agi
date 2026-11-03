import test from 'node:test';
import assert from 'node:assert/strict';
import {randomBytes} from 'node:crypto';
import {MAX_CHECKPOINT_BYTES, processFeatureClaim, processGroupingClaim, engineInput, groupingFailureCode} from './coordinator.mjs';
import {createControlClient} from '../control-client.mjs';
import {makeGroupingSnapshotFixture} from './snapshot-fixture.mjs';
import {adaptGroupingSnapshot, canonicalSnapshotDigest} from './snapshot.mjs';
import {FEATURE_VERSION, SIMULATION_FEATURE_VERSION, featureDigest} from './features.mjs';
import {createGroupingInvestigator} from './gateway.mjs';
import {SIMULATION_F6_MINILM_POLICY} from './policy.mjs';

const id='11111111-1111-4111-8111-111111111111';
const model={name:'all-MiniLM-L6-v2',dimension:384,servingRelease:'test'};
// Still over the bound once compressed.
const incompressible=()=>randomBytes(MAX_CHECKPOINT_BYTES).toString('base64');
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

test('simulation feature policy cannot consume trace reports', async () => {
  let embeddings = 0;
  await assert.rejects(() => processFeatureClaim({...claim(), feature_attempt_id: id,
    policy_version: SIMULATION_FEATURE_VERSION}, {model,
    embedBatch: async () => { embeddings++; throw new Error('Unexpected embedding request'); },
    control: async () => ({state: 'failed'}),
  }), /Simulation features require simulation reports/);
  assert.equal(embeddings, 0);
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
      ? {checkpoint_revision:body.expected_revision+1}:{status:'completed',registry_revision:4};},
    createInvestigator:async()=>({investigate:()=>{},receiptIds:()=>['new-receipt']}),
    engine:async({store})=>{
      for(const name of ['checkpoint.json','predictions.json','registry.json','prediction-receipt.json']){
        await store.save(name,{test:true});assert.deepEqual(await store.read(name),{test:true});
      }
      return {status:'complete',commands:[],dispositions:[{state:'deferred',occurrence_id:'a',reason:'unknown'}]};
    },
  });
  assert.deepEqual(result,{status:'completed',registry_revision:4});
  const publication=requests.at(-1).body;
  assert.equal(publication.snapshot_digest,'cohort-digest');
  assert.deepEqual(publication.receipt_ids,['new-receipt','prior-receipt']);
  assert.deepEqual(publication.commands,[{type:'defer',occurrence_ids:['a'],reason:'unknown'}]);
});

test('paused algorithm cannot publish partial assignments',async()=>{
  let published=false;
  await assert.rejects(()=>processGroupingClaim(claim(),{
    control:async path=>{if(path.endsWith('publish/'))published=true;return {state:'cancelled'};},
    createInvestigator:async()=>({investigate:()=>{},receiptIds:()=>[]}),
    engine:async()=>({status:'paused'}),
  }),/no Feed publication/);
  assert.equal(published,false);
});

test('model-backed commands without a known per-command receipt never publish',async()=>{
  let published=false;
  await assert.rejects(()=>processGroupingClaim(claim(),{
    control:async path=>{if(path.endsWith('publish/'))published=true;return {state:'cancelled'};},
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
      await store.save('checkpoint.json',{large_source:incompressible()});
      return {status:'complete',commands:[],dispositions:[]};
    },
  }),/checkpoint exceeds bound/);
  assert.deepEqual(writes,[`/grouping/attempts/${id}/`]);
});

test('oversized trace checkpoint is compressed before write',async()=>{
  const writes=[];
  const control=async(path,payload)=>{
    if(path.endsWith('checkpoint/')){writes.push(payload.checkpoint);return {checkpoint_revision:payload.expected_revision+1};}
    if(path.endsWith('publish/'))return {status:'completed',registry_revision:4};
    return {checkpoint_revision:1};
  };
  const work=claim();
  assert.deepEqual(await processGroupingClaim(work,{control,
    createInvestigator:async()=>({investigate:()=>{},receiptIds:()=>[]}),
    engine:async({store})=>{
      await store.save('checkpoint.json',{large_source:'x'.repeat(MAX_CHECKPOINT_BYTES)});
      return {status:'complete',commands:[],dispositions:[{state:'deferred',
        occurrence_id:work.pending_ids[0],reason:'No supported mechanism'}]};
    },
  }),{status:'completed',registry_revision:4});
  assert.ok(writes.length>0);
  assert.ok(writes.every(checkpoint=>checkpoint.encoding==='grouping-checkpoint-gzip/v1'));
});

test('lost publication response is reconciled as success when backend already completed', async()=>{
  const client=createControlClient({baseUrl:'https://example.test',token:'test',
    fetchImpl:async()=>{throw new DOMException('Timeout','TimeoutError');}});
  let failure;
  const result=await processGroupingClaim(claim(),{
    control:async(path,body,options)=>{
      if(path.endsWith('checkpoint/'))return {checkpoint_revision:body.expected_revision+1};
      if(path.endsWith('publish/'))return client(path,body,options);
      failure=body;return {state:'completed'};
    },
    createInvestigator:async()=>({investigate:()=>{},receiptIds:()=>[]}),
    engine:async()=>({status:'complete',commands:[],dispositions:[]}),
  });
  assert.deepEqual(result,{published:true,reconciled:true});
  assert.equal(failure.action,'fail');
  assert.equal(failure.failure_code,'control_network_error');
  assert.ok(!JSON.stringify(failure).includes('Timeout'));
});

test('shutdown cleanup uses a live bounded signal and preserves the original error', async()=>{
  const stop=new AbortController();stop.abort();
  const original=new Error('private source text must not enter failure payload');
  let payload;
  await assert.rejects(()=>processGroupingClaim(claim(),{
    signal:stop.signal,
    control:async(path,body,options)=>{payload=body;assert.equal(options.signal.aborted,false);throw new Error('cleanup offline');},
    createInvestigator:async()=>({investigate:()=>{},receiptIds:()=>[]}),
    engine:async()=>{throw original;},
  }), error=>error===original);
  assert.equal(payload.failure_code,'worker_cancelled');
  assert.ok(!JSON.stringify(payload).includes('private source'));
});

test('paused work reports a stable nonretryable failure code',async()=>{
  let payload;
  await assert.rejects(()=>processGroupingClaim(claim(),{
    control:async(path,body)=>{payload=body;return {state:'cancelled'};},
    createInvestigator:async()=>({investigate:()=>{},receiptIds:()=>[]}),
    engine:async()=>({status:'paused'}),
  }),/no Feed publication/);
  assert.equal(payload.failure_code,'grouping_paused');
});

test('shutdown preserves an explicit unknown-usage failure in backend cleanup',async()=>{
  const stop=new AbortController();stop.abort();
  const original=Object.assign(new Error('synthetic usage failure'),{groupingFailureCode:'gateway_unknown_usage'});
  let payload;
  await assert.rejects(()=>processGroupingClaim(claim(),{
    signal:stop.signal,
    control:async(path,body,options)=>{payload=body;assert.equal(options.signal.aborted,false);return {state:'cancelled'};},
    createInvestigator:async()=>({investigate:()=>{},receiptIds:()=>[]}),
    engine:async()=>{throw original;},
  }),error=>error===original);
  assert.equal(payload.failure_code,'gateway_unknown_usage');
  for(const code of ['grouping_paused','context_budget']){
    assert.equal(groupingFailureCode({groupingFailureCode:code},stop.signal),code);
  }
});

test('control HTTP failures are classified without forwarding response or exception text',async()=>{
  for(const [status,code] of [[409,'control_rejected'],[429,'control_server_error'],[503,'control_server_error'],[403,'control_rejected']]){
    const client=createControlClient({baseUrl:'https://example.test',token:'test',fetchImpl:async()=>new Response('private upstream body',{status})});
    try{await client(`/grouping/attempts/${id}/publish/`,{});assert.fail('expected failure');}
    catch(error){assert.equal(groupingFailureCode(error),code);}
  }
  assert.equal(groupingFailureCode(new Error('private text')),'grouping_worker_error');
});

test('only recognized stale-state conflicts retry; persistent and unknown conflicts park',async()=>{
  const stop=new AbortController();stop.abort();
  for(const [reason,code] of [
    ['grouping publication read set is stale','grouping_conflict'],
    ['candidate registry changed while building claim','grouping_conflict'],
    ['candidate context exceeds response bound','context_budget'],
    ['grouping project budget exhausted or disabled','grouping_paused'],
    ['unknown call receipt cannot be overwritten','gateway_unknown_usage'],
    ['refresh target is stale or not offered','control_rejected'],
    ['publication key was reused for a different proposal','control_rejected'],
    ['private provider text','control_rejected'],
  ]){
    const client=createControlClient({baseUrl:'https://example.test',token:'test',
      fetchImpl:async()=>Response.json({code:'grouping_conflict',detail:reason},{status:409})});
    await assert.rejects(()=>client(`/grouping/attempts/${id}/publish/`,{}),error=>{
      assert.equal(groupingFailureCode(error),code);
      assert.equal(groupingFailureCode(error,stop.signal),code);
      return true;
    });
  }
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
    if(path.endsWith('publish/')){published++;return {status:'completed',registry_revision:4};}
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
        ? {large_source:incompressible()}:{phase:'complete'});
      return {status:'complete',commands:[],dispositions:[{state:'deferred',
        occurrence_id:work.pending_ids[0],reason:'No supported mechanism'}]};
    }};
  await assert.rejects(()=>processGroupingClaim(work,options),/checkpoint exceeds bound/);
  assert.equal(paidCalls,1);
  assert.equal(published,0);
  failCheckpoint=false;
  assert.deepEqual(await processGroupingClaim(work,options),{status:'completed',registry_revision:4});
  assert.equal(paidCalls,1);
  assert.equal(published,1);
});


test('failed optional merge does not poison publication of a valid assignment',async()=>{
  const work={...claim(),policy_version:'f6-minilm-sampled/v2',receipt_ids:[]};
  const valid='22222222-2222-4222-8222-222222222222';
  const failed='33333333-3333-4333-8333-333333333333';
  const settlements=new Map();let paid=0,published;
  const control=async(path,body)=>{
    if(path.endsWith('reserve/'))return {created:true,status:'reserved',
      receipt_id:body.request_key.startsWith('merge-review:')?failed:valid,
      request_digest:body.request_digest};
    if(path.endsWith('settle/')){
      settlements.set(body.request_key.startsWith('merge-review:')?failed:valid,body);
      return {status:body.status};
    }
    if(path.endsWith('checkpoint/'))return {checkpoint_revision:body.expected_revision+1};
    if(path.endsWith('publish/')){
      // Mirror the backend's receipt eligibility check, including null results.
      assert.ok(body.receipt_ids.every(key=>settlements.get(key)?.result!=null));
      published=body;return {status:'completed',registry_revision:4};
    }
    throw new Error('Unexpected control path');
  };
  const createProvider=()=>{
    const calls=[];
    return {accounting:()=>({calls}),provider:{generate:async()=>{
      paid++;calls.push({cost_microusd:100,routed_model:'google/gemini-3.8-flash'});
      return {content:paid===1?'{}':'invalid JSON',raw:{choices:[{finish_reason:'stop'}]}};
    }}};
  };
  await processGroupingClaim(work,{control,model,reserveUsd:0.1,
    gatewayConfig:{model:'google/gemini-3.8-flash'},
    createInvestigator:args=>createGroupingInvestigator({...args,createProvider,
      countRequest:async()=>({input_tokens:100,request_bytes:1000}),onDiagnostic:()=>{}}),
    engine:async({investigate})=>{
      const result=await investigate({task:'discovery'},{});
      const receipt=investigate.receiptFor(result);
      await assert.rejects(investigate({candidate:{type:'merge_review'}},{}),{name:'GroupingProviderFailure'});
      return {status:'complete',commands:[{type:'attach',issue_id:'existing',
        expected_issue_revision:1,occurrence_ids:work.pending_ids,citations:[],
        admission:{primary_receipt_id:receipt,group_index:0,repair_receipt_id:null}}],dispositions:[]};
    }});
  assert.deepEqual(published.receipt_ids,[valid]);
  assert.equal(published.commands[0].type,'attach');
  assert.equal(settlements.get(failed).result,null);
  assert.equal(settlements.get(failed).cost_usd,'0.000100000');
});

test('lost publish acknowledgement retries the identical frozen payload without inference', async () => {
  const sends=[]; let engineCalls=0;
  const result=await processGroupingClaim(claim(), {
    control:async(path,body)=>{
      if (path.endsWith('checkpoint/')) return {checkpoint_revision:body.expected_revision+1};
      sends.push(structuredClone(body));
      if (sends.length===1) throw new Error('connection closed after database commit');
      return {status:'completed',registry_revision:4};
    },
    createInvestigator:async()=>({investigate:()=>{},receiptIds:()=>[]}),
    engine:async()=>{
      engineCalls++;
      return {status:'complete',commands:[],dispositions:[{state:'deferred',occurrence_id:'a',reason:'uncertain'}]};
    },
  });
  assert.equal(result.status,'completed');
  assert.equal(engineCalls,1);
  assert.deepEqual(sends[0],sends[1]);
});

test('recovered publication reuses original receipts and bypasses model work', async () => {
  const base=claim();
  const binding={snapshot_digest:base.snapshot_digest,candidate_digest:base.candidate_digest,
    registry_revision:base.registry_revision,policy_version:base.policy_version};
  const body={idempotency_key:'saved',snapshot_digest:base.snapshot_digest,registry_revision:3,
    commands:[{type:'defer',occurrence_ids:['a'],reason:'uncertain'}],receipt_ids:[]};
  base.checkpoint={files:{'publication.json':{binding,body}}};
  let sent;
  await processGroupingClaim(base,{
    createInvestigator:async()=>{throw new Error('model work resumed');},
    engine:async()=>{throw new Error('engine resumed');},
    control:async(_path,payload)=>{sent=payload;return {status:'completed',registry_revision:4};},
  });
  assert.deepEqual(sent,{...body,lease_token:base.lease_token});
});

test('a publication validation conflict is not retried', async () => {
  let sends=0;
  await assert.rejects(()=>processGroupingClaim(claim(),{
    createInvestigator:async()=>({investigate:()=>{},receiptIds:()=>[]}),
    engine:async()=>({status:'complete',commands:[],dispositions:[{state:'deferred',occurrence_id:'a',reason:'uncertain'}]}),
    control:async(path,body)=>{
      if(path.endsWith('checkpoint/'))return {checkpoint_revision:body.expected_revision+1};
      if(body.action === 'fail')return {state:'cancelled'};
      sends++;throw Object.assign(new Error('stale publication'),{status:409});
    },
  }),/stale publication/);
  assert.equal(sends,1);
});


test('sampled recovery retains full source snapshots but processes only selected pending findings',()=>{
  const base=claim();
  const other=structuredClone(base.snapshot);
  other.report.id='22222222-2222-4222-8222-222222222222';
  other.occurrences[0].occurrence_id='33333333-3333-4333-8333-333333333333';
  const {snapshot_digest,...body}=other; other.snapshot_digest=canonicalSnapshotDigest(body);
  const work={...base,policy_version:'f6-minilm-sampled/v2',pending_snapshots:[base.snapshot,other]};
  const input=engineInput(work,model);
  assert.deepEqual(input.pendingIds,base.pending_ids);
  assert.equal(input.rows.length,1);
  assert.throws(()=>engineInput({...work,pending_ids:[]},model),/membership mismatch/);
  assert.throws(()=>engineInput({...work,pending_ids:['not-in-snapshot']},model),/membership mismatch/);
  assert.throws(()=>engineInput({...work,policy_version:'f6-minilm/v1'},model),/membership mismatch/);
});

test('100 singleton suggestions publish admitted results at the call cap without an unsent reservation', async () => {
  const work = claim(), snapshot = makeGroupingSnapshotFixture(), report = snapshot.report;
  report.workload_type = 'simulation_test_execution'; report.test_execution_id = id; report.trace_id = null;
  report.coverage = {scope: 'simulation_test_execution', observed_call_count: 1, read_complete: true};
  report.evidence_receipts[0] = {...report.evidence_receipts[0], span_id: null, parent_span_id: null, call_execution_id: id};
  const original = report.findings[0];
  report.findings = Array.from({length: 100}, (_, number) => ({...structuredClone(original),
    finding_id: `finding-${number}`, statement: `Operation ${number} used an incorrect amount.`,
    attribution: {origin: null, decisive: {status: 'supported', span_id: null, call_execution_id: id, evidence_ids: ['evidence-1']},
      symptom: {status: 'unknown', span_id: null, evidence_ids: []}}}));
  snapshot.occurrences = report.findings.map((finding, number) => ({finding_id: finding.finding_id,
    occurrence_id: `00000000-0000-4000-8000-${String(number).padStart(12, '0')}`}));
  const snapshots = snapshot.occurrences.map((occurrence, number) => {
    const body = {contract_version: snapshot.contract_version,
      report: {...report, id: `22222222-2222-4222-8222-${String(number).padStart(12, '0')}`,
        findings: [report.findings[number]]}, occurrences: [occurrence]};
    return {...body, snapshot_digest: canonicalSnapshotDigest(body)};
  });
  const rows = snapshots.flatMap(adaptGroupingSnapshot);
  Object.assign(work, {snapshot: snapshots[0], pending_snapshots: snapshots, pending_ids: rows.map(row => row.id),
    policy_version: SIMULATION_F6_MINILM_POLICY.version, receipt_ids: [],
    features: rows.map((row, number) => {
      const entry = {occurrence_id: row.id, view: 'semantics', source_digest: row.source_digest,
        evidence_revision: row.evidence_revision, text_digest: featureDigest(row.views.semantics),
        model: model.name, model_revision: null, serving_release: model.servingRelease, dimension: 384,
        vector: Array.from({length: 384}, (_, index) => index === number ? 1 : 0), index_buckets: []};
      return {...entry, feature_digest: featureDigest(entry)};
    })});
  const reservations = [], settlements = [], diagnostics = [];
  let publication, sent = 0, checkpoint;
  const control = async (path, payload) => {
    if (path.endsWith('reserve/')) {
      reservations.push(payload);
      return {created: true, status: 'reserved', request_digest: payload.request_digest,
        receipt_id: `11111111-1111-4111-8111-${String(reservations.length).padStart(12, '0')}`};
    }
    if (path.endsWith('settle/')) { settlements.push(payload); return {status: 'settled'}; }
    if (path.endsWith('checkpoint/')) { checkpoint = payload; return {checkpoint_revision: payload.expected_revision + 1}; }
    if (path.endsWith('publish/')) { publication = payload; return {status: 'completed', registry_revision: 4}; }
    assert.fail(`Unexpected control request ${path}`);
  };
  const createProvider = ({maxCalls, fetchImpl}) => {
    const calls = [];
    return {accounting: () => ({calls}), provider: {generate: async () => {
      if (calls.length >= maxCalls) throw new Error('Model-call budget exhausted');
      const response = await fetchImpl('http://gateway/v1/chat/completions', {});
      calls.push({cost_microusd: 100, routed_model: 'google/gemini-3.8-flash'});
      return {content: await response.text(), raw: {choices: [{finish_reason: 'stop'}]}};
    }}};
  };
  const result = await processGroupingClaim(work, {control, model, reserveUsd: 0.1,
    gatewayConfig: {model: 'google/gemini-3.8-flash'},
    createInvestigator: args => createGroupingInvestigator({...args, createProvider,
      countRequest: async () => ({input_tokens: 100, request_bytes: 1000}),
      onDiagnostic: event => diagnostics.push(event), fetchImpl: async (_url, init) => {
        sent++;
        const prompt = JSON.parse(JSON.parse(init.body).messages[1].content);
        if (prompt.instructions.startsWith('Prepare candidate comparisons')) {
          return Response.json({groups: prompt.findings.map(finding => ({finding_ids: [finding.id],
            behavior: `Incorrect amount for ${finding.id}`, fix_hypothesis: `Correct ${finding.id}`}))});
        }
        assert.equal(prompt.findings.length, 1);
        const finding = prompt.findings[0], source = finding.evidence.find(item => item.id === 'report');
        return Response.json({groups: [{target_issue_id: null, member_ids: [finding.id],
          title: 'Operation uses an incorrect amount', mechanism: `Incorrect amount for ${finding.id}`,
          fix_hypothesis: `Validate the amount for ${finding.id}`, falsifier: 'The operation used the correct amount',
          predicted_observations: ['The amount differs from the requested amount'],
          citations: [{finding_id: finding.id, evidence_id: 'report', evidence_digest: source.digest, quote: source.text}],
          contradictions: [], alternatives: ['A display issue alone does not explain the executed amount'], missing_evidence: []}], deferred: []});
      }}),
  });
  assert.equal(result.status, 'completed');
  assert.equal(sent, 100); assert.equal(reservations.length, 100); assert.equal(settlements.length, 100);
  assert.ok(settlements.every(settlement => settlement.status === 'settled'));
  assert.equal(publication.receipt_ids.length, 100);
  const admitted = publication.commands.filter(command => command.type === 'create');
  const deferred = publication.commands.filter(command => command.type === 'defer');
  assert.equal(admitted.length, 99); assert.equal(deferred.length, 1);
  assert.equal(deferred[0].reason, 'budget_exhausted:work_calls');
  assert.deepEqual(new Set([...admitted, ...deferred].flatMap(command => command.occurrence_ids)), new Set(work.pending_ids));
  assert.ok(checkpoint);
  assert.ok(diagnostics.some(event => event.event === 'grouping_budget_paused' && event.limit === 'work_calls'));
  assert.equal(checkpoint.checkpoint.encoding, 'grouping-checkpoint-gzip/v1');
  const originalPublication = structuredClone(publication);
  await processGroupingClaim({...work, checkpoint: checkpoint.checkpoint}, {control, model,
    createInvestigator: async () => assert.fail('Frozen publication must resume without inference')});
  assert.deepEqual(publication, originalPublication);
  assert.equal(sent, 100);
});
