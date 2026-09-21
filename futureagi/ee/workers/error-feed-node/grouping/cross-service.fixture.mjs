// Test-only JSON stdin/stdout bridge for backend admission contract tests.
// Deliberately absent from the worker image manifest: no gateway or model call.
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';
import {processGroupingClaim} from './coordinator.mjs';
import {featureDigest} from './features.mjs';

const MAX_INPUT_BYTES = 8 * 1024 * 1024;
const MAX_OUTPUT_BYTES = 16 * 1024 * 1024;
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;

function syntheticGroup(finding) {
  const report = finding.evidence?.find(item => item.id === 'report');
  assert.ok(report?.text && report.digest, 'Fixture needs an accepted report citation');
  return {target_issue_id:null,member_ids:[finding.id],
    mechanism:`Accepted occurrence ${finding.id} reports a distinct failure`,
    fix_hypothesis:'Correct the action described by the accepted finding report',
    falsifier:'A current captured execution of the same action does not reproduce the reported failure',
    predicted_observations:['The reported action has the stated failure on recurrence'],
    citations:[{finding_id:finding.id,evidence_id:'report',
      evidence_digest:report.digest,quote:report.text}],
    contradictions:[],alternatives:['A report-only rendering defect is an alternative mechanism'],
    missing_evidence:[]};
}

function fixtureResult(prompt) {
  if (prompt.candidate) return {action:'hold',groups:[],removed_ids:[],
    reason:'Synthetic fixture does not support a topology change'};
  if (prompt.instructions?.startsWith('CITATION-ONLY REPAIR.')) {
    throw new Error('Unexpected citation repair in fully cited fixture');
  }
  const findings=prompt.findings ?? [];
  if (prompt.instructions?.includes('TARGETED LATE REVISIT:')
      || prompt.instructions?.includes('FOCUSED ATTACHMENT:')) {
    return {groups:[],deferred:findings.map(item=>({finding_id:item.id,
      reason:'Synthetic fixture cannot prove attachment to an existing issue'}))};
  }
  return {groups:findings.map(syntheticGroup),deferred:[]};
}

export async function runCrossServiceFixture({claim, receipt_ids_override: overrides = null}) {
  assert.ok(claim && typeof claim === 'object' && !Array.isArray(claim), 'Grouping claim required');
  assert.ok(overrides === null || Array.isArray(overrides)
    && overrides.length <= 100 && overrides.every(id=>typeof id==='string'&&UUID.test(id))
    && new Set(overrides).size === overrides.length, 'Invalid fixture receipt override');
  const release=claim.features?.[0]?.serving_release;
  assert.ok(typeof release==='string' && release, 'Fixture claim needs persisted feature release');
  const rawResults=[];
  const seen=new Map();
  const resultReceipts=new WeakMap();
  let checkpointRevision=claim.checkpoint_revision??0;
  let checkpointCount=0, finalCheckpoint=claim.checkpoint??{};
  let publishBody=null;
  const investigate=async(prompt,schema,_evidenceRows,{repairIntent=null}={})=>{
    const requestDigest='sha256:'+featureDigest({prompt,schema,candidate_digest:claim.candidate_digest,
      repair_intent:repairIntent});
    const prior=seen.get(requestDigest);
    if(prior){
      const replay=structuredClone(prior.result);
      resultReceipts.set(replay,prior.receipt_id);
      return replay;
    }
    const index=rawResults.length;
    const receiptId=overrides?.[index]
      ?? `00000000-0000-4000-8000-${String(index+1).padStart(12,'0')}`;
    assert.ok(UUID.test(receiptId), 'Fixture receipt identity invalid');
    const result=fixtureResult(prompt);
    const record={receipt_id:receiptId,request_digest:requestDigest,
      repair_intent:repairIntent,result};
    seen.set(requestDigest,record);
    rawResults.push(record);
    resultReceipts.set(result,receiptId);
    return result;
  };
  investigate.receiptFor=result=>resultReceipts.get(result)??null;
  const control=async(path,body)=>{
    if(path.endsWith('/checkpoint/')){
      assert.equal(body.expected_revision,checkpointRevision,'Fixture checkpoint revision changed');
      checkpointRevision++;
      checkpointCount++;
      finalCheckpoint=structuredClone(body.checkpoint);
      return {checkpoint_revision:checkpointRevision};
    }
    if(path.endsWith('/publish/')){
      assert.equal(publishBody,null,'Fixture published twice');
      publishBody=structuredClone(body);
      return {published:true};
    }
    if(body?.action==='renew')return {state:'claimed'};
    throw new Error('Unexpected fixture control operation');
  };
  await processGroupingClaim(claim,{control,model:{name:'all-MiniLM-L6-v2',dimension:384,
    servingRelease:release},createInvestigator:async()=>({investigate,
    receiptIds:()=>rawResults.map(item=>item.receipt_id)})});
  assert.ok(publishBody,'Fixture did not reach publish boundary');
  if(overrides)assert.equal(overrides.length,rawResults.length,'Fixture receipt override count changed');
  const output={protocol:'grouping-cross-service-fixture/v1',raw_results:rawResults,
    receipt_ids:rawResults.map(item=>item.receipt_id),
    publish_body:publishBody,checkpoint_count:checkpointCount,
    checkpoint_revision:checkpointRevision,final_checkpoint:finalCheckpoint};
  assert.ok(Buffer.byteLength(JSON.stringify(output))<=MAX_OUTPUT_BYTES,'Fixture output exceeds bound');
  return output;
}

async function readInput() {
  const chunks=[];
  let bytes=0;
  for await (const chunk of process.stdin) {
    bytes+=chunk.length;
    if(bytes>MAX_INPUT_BYTES)throw new Error('Fixture input exceeds bound');
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
  try{process.stdout.write(JSON.stringify(await runCrossServiceFixture(await readInput()))+'\n');}
  catch(error){process.stdout.write(JSON.stringify({error:error.message})+'\n');process.exitCode=1;}
}
