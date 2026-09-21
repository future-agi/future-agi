import {setTimeout as delay} from 'node:timers/promises';
import {adaptGroupingSnapshot} from './snapshot.mjs';
import {buildFeatures, featureDigest, FEATURE_VERSION} from './features.mjs';
import {createF6Planes, bucketRowsForFeature} from './lsh.mjs';
import {runGrouping} from './engine.mjs';
import {F6_MINILM_POLICY} from './policy.mjs';
import {createGroupingInvestigator} from './gateway.mjs';
import {validateEmbeddingModel} from './embedding-client.mjs';

const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;

async function withLease(claim, path, {control, signal, heartbeatMs = 15000}, run) {
  if (typeof claim.lease_token !== 'string' || !claim.lease_token) throw new Error('Missing lease');
  const stop = new AbortController(), revoked = new AbortController();
  const runSignal = AbortSignal.any([revoked.signal, ...(signal ? [signal] : [])]);
  let leaseError;
  const heartbeats = (async () => {
    try {
      while (!stop.signal.aborted) {
        await delay(heartbeatMs, null, {signal:stop.signal});
        await control(path, {lease_token:claim.lease_token,action:'renew'}, {method:'PATCH',signal:runSignal});
      }
    } catch (error) {
      if (!stop.signal.aborted) { leaseError = error; revoked.abort(); }
    }
  })();
  try {
    const result = await run(runSignal);
    if (leaseError) throw leaseError;
    return result;
  } finally { stop.abort(); await heartbeats; }
}

export async function processFeatureClaim(claim, options) {
  const {control, embedBatch, model} = options;
  if (!UUID.test(claim.feature_attempt_id ?? '') || claim.policy_version !== FEATURE_VERSION) {
    throw new Error('Unsupported feature claim');
  }
  const path = `/grouping/feature-attempts/${claim.feature_attempt_id}/`;
  return withLease(claim, path, options, async signal => {
    try {
      const rows = adaptGroupingSnapshot(claim.snapshot);
      const prepared = await buildFeatures(rows, {embedBatch,model,signal});
      const planes = createF6Planes(prepared.dimension);
      const features = rows.flatMap(row => Object.entries(prepared.rows[row.id].views).map(([view, feature]) => {
        const record = {occurrence_id:row.id,view,source_digest:row.source_digest,
          evidence_revision:row.evidence_revision,text_digest:feature.text_digest,
          model:prepared.model,model_revision:null,serving_release:prepared.serving_release,
          dimension:prepared.dimension,vector:feature.vector,
          index_buckets:bucketRowsForFeature(row,view,feature.vector,planes)
            .map(({table,signature})=>({table,signature}))};
        return {...record,feature_digest:featureDigest(record)};
      }));
      signal.throwIfAborted();
      return await control(path+'complete/', {lease_token:claim.lease_token,status:'ready',features}, {signal});
    } catch (error) {
      // A failed acknowledgement may have committed. Never replace a ready
      // result with failure; the server's idempotent/fenced transition decides.
      try { await control(path+'complete/', {lease_token:claim.lease_token,status:'failed',
        features:[],error_code:signal.aborted?'worker_cancelled':'feature_preparation_failed'}); }
      catch { /* Original failure is surfaced; durable lease expiry recovers work. */ }
      throw error;
    }
  });
}

export function engineInput(claim, configuredModel) {
  const pendingSnapshots = claim.pending_snapshots ?? [claim.snapshot];
  if (!Array.isArray(pendingSnapshots) || pendingSnapshots.length > 20) throw new Error('Invalid pending snapshot count');
  const pendingRows = pendingSnapshots.flatMap(adaptGroupingSnapshot);
  const pendingIds = pendingRows.map(row=>row.id);
  if (new Set(pendingIds).size !== pendingIds.length || !Array.isArray(claim.pending_ids)
      || featureDigest([...pendingIds].sort()) !== featureDigest([...claim.pending_ids].sort())) {
    throw new Error('Claimed pending membership mismatch');
  }
  const candidates = claim.candidate_window;
  const memberIds = new Set(candidates?.issues?.flatMap(issue=>issue.members.map(member=>member.occurrence_id)) ?? []);
  const candidateSnapshots = claim.candidate_snapshots ?? [];
  if (!Array.isArray(candidateSnapshots) || candidateSnapshots.length > 320) throw new Error('Invalid candidate snapshot count');
  const byId = new Map(pendingRows.map(row=>[row.id,row]));
  for (const row of candidateSnapshots.flatMap(adaptGroupingSnapshot)) {
    if (!memberIds.has(row.id)) continue;
    if (byId.has(row.id) && featureDigest(byId.get(row.id)) !== featureDigest(row)) throw new Error('Conflicting occurrence snapshots');
    byId.set(row.id,row);
  }
  if (!candidates || candidates.registry_revision !== claim.registry_revision) {
    throw new Error('Claim candidate registry revision mismatch');
  }
  if (!Array.isArray(claim.features) || claim.features.length > 846) throw new Error('Invalid feature window');
  const expectedModel = claim.features.length ? validateEmbeddingModel(configuredModel) : null;
  const features = {model:'all-MiniLM-L6-v2',dimension:384,rows:{}};
  for (const entry of claim.features) {
    const row = byId.get(entry.occurrence_id);
    if (!row) throw new Error('Feature outside claimed window');
    if (entry.source_digest !== row.source_digest || entry.evidence_revision !== row.evidence_revision
        || !['semantics','task'].includes(entry.view)) throw new Error('Stale claimed feature');
    const {feature_digest, ...record} = entry;
    if (entry.model !== expectedModel.name || entry.dimension !== expectedModel.dimension
        || entry.serving_release !== expectedModel.servingRelease || entry.model_revision !== null
        || typeof feature_digest !== 'string' || feature_digest !== featureDigest(record)) {
      throw new Error('Claimed feature representation mismatch');
    }
    const target = features.rows[row.id] ??= {source_digest:row.source_digest,evidence_revision:row.evidence_revision,views:{}};
    if (target.views[entry.view]) throw new Error('Duplicate view feature');
    target.views[entry.view] = {vector:entry.vector,text_digest:entry.text_digest};
  }
  return {rows:[...byId.values()],pendingIds,features,candidateWindow:candidates,
    cannotLinks:claim.constraints ?? []};
}

function checkpointStore(claim, control, signal) {
  let revision = claim.checkpoint_revision ?? 0;
  const files = structuredClone(claim.checkpoint?.files ?? {});
  return {
    read: async name => structuredClone(files[name] ?? null),
    save: async (name,value) => {
      if (!['checkpoint.json','predictions.json','registry.json','prediction-receipt.json'].includes(name)) {
        throw new Error('Unsupported grouping checkpoint file');
      }
      const next = {...files,[name]:structuredClone(value)};
      if (Buffer.byteLength(JSON.stringify(next)) > 2*1024*1024) throw new Error('Grouping checkpoint exceeds bound');
      const reply = await control(`/grouping/attempts/${claim.attempt_id}/checkpoint/`, {
        lease_token:claim.lease_token,expected_revision:revision,checkpoint:{files:next},
      }, {method:'PUT',signal});
      if (reply.checkpoint_revision !== revision+1) throw new Error('Checkpoint was not acknowledged');
      revision = reply.checkpoint_revision;
      Object.assign(files,next);
    },
  };
}

function validateCommandProvenance(commands, receiptIds) {
  const known = new Set(receiptIds);
  const admission = (value, groupRequired = true) => {
    if (!value || !UUID.test(value.primary_receipt_id ?? '')
        || !known.has(value.primary_receipt_id)) throw new Error('Missing durable grouping admission receipt');
    if (groupRequired && (!Number.isSafeInteger(value.group_index) || value.group_index < 0
        || value.repair_receipt_id !== null
          && (!UUID.test(value.repair_receipt_id ?? '') || !known.has(value.repair_receipt_id)))) {
      throw new Error('Invalid grouping admission provenance');
    }
  };
  for (const command of commands) {
    if (['create','attach','merge'].includes(command.type)) admission(command.admission);
    if (command.type === 'split') {
      if (!Array.isArray(command.admissions) || command.admissions.length !== command.parts?.length) {
        throw new Error('Incomplete split admission provenance');
      }
      command.admissions.forEach(item => admission(item));
    }
    if (command.type === 'remove') admission(command.admission,false);
  }
}

export async function processGroupingClaim(claim, options) {
  const {control, gatewayConfig, reserveUsd, createInvestigator = createGroupingInvestigator,
    engine = runGrouping} = options;
  if (!UUID.test(claim.attempt_id ?? '')) throw new Error('Invalid grouping attempt');
  if (claim.policy_version !== F6_MINILM_POLICY.version) throw new Error('Unsupported grouping policy');
  const path = `/grouping/attempts/${claim.attempt_id}/`;
  return withLease(claim,path,options,async signal => {
    const input = engineInput(claim, options.model);
    const gateway = await createInvestigator({claim,control,config:gatewayConfig,reserveUsd,signal});
    const result = await engine({...input,investigate:gateway.investigate,
      store:checkpointStore(claim,control,signal)});
    if (result.status !== 'complete') throw new Error('Grouping paused with durable checkpoint; no Feed publication');
    const commands = [...result.commands,...result.dispositions.filter(item=>item.state==='deferred')
      .map(item=>({type:'defer',occurrence_ids:[item.occurrence_id],reason:item.reason}))];
    const receiptIds=[...new Set([...(claim.receipt_ids ?? []),...gateway.receiptIds()])];
    validateCommandProvenance(commands,receiptIds);
    signal.throwIfAborted();
    return control(path+'publish/',{lease_token:claim.lease_token,
      idempotency_key:featureDigest({attempt:claim.attempt_id,commands}),
      snapshot_digest:claim.snapshot_digest,registry_revision:claim.registry_revision,
      commands,receipt_ids:receiptIds}, {signal});
  });
}

// Separate pools ensure a long LLM run never prevents immediate feature work.
// Kafka only wakes durable work; polling remains the recovery path.
export async function runGroupingCoordinator({control,workerId,signal,pollMs=1000,
  featureConcurrency=2,groupingConcurrency=1,onError,wakeup,...options}) {
  for (const count of [featureConcurrency,groupingConcurrency]) {
    if (!Number.isSafeInteger(count)||count<1||count>10) throw new Error('Invalid grouping concurrency');
  }
  if (!workerId || !signal || typeof onError !== 'function' || !Number.isSafeInteger(pollMs)||pollMs<100) {
    throw new Error('Invalid coordinator configuration');
  }
  const pools = [
    {path:'/grouping/feature-claims/',capacity:featureConcurrency,key:'feature_attempt_id',process:processFeatureClaim,active:new Map()},
    {path:'/grouping/claims/',capacity:groupingConcurrency,key:'attempt_id',process:processGroupingClaim,active:new Map()},
  ];
  try {
    while (!signal.aborted) {
      let healthy = true;
      for (const pool of pools) {
        if (pool.active.size >= pool.capacity) continue;
        try {
          const reply = await control(pool.path,{worker_id:workerId,limit:pool.capacity-pool.active.size},{signal});
          if (!Array.isArray(reply.claims)||reply.claims.length>pool.capacity-pool.active.size) throw new Error('Invalid claim response');
          for (const claim of reply.claims) {
            const id=claim[pool.key];
            if (!UUID.test(id??'')||pool.active.has(id)) throw new Error('Duplicate or invalid attempt');
            const task=pool.process(claim,{...options,control,signal})
              .catch(error=>onError(error,id)).finally(()=>pool.active.delete(id));
            pool.active.set(id,task);
          }
        } catch(error) { healthy = false; if(!signal.aborted) onError(error); }
      }
      if (healthy) wakeup?.acknowledge();
      if (wakeup) await wakeup.wait(pollMs,signal);
      else await delay(pollMs,null,{signal});
    }
  } catch(error) { if(!signal.aborted) throw error; }
  finally { await Promise.allSettled(pools.flatMap(pool=>[...pool.active.values()])); }
}
