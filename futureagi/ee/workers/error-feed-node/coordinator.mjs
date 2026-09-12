import {mkdir, open, rename, readdir, readFile, unlink, stat} from 'node:fs/promises';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {validateClaim} from './evidence-store.mjs';

// An offset is acknowledged only after Django's transaction has committed.
// Invalid events stop this batch: do not silently skip a poisoned tenant event.
export async function recordKafkaBatch({batch, resolveOffset, heartbeat, isRunning, isStale}, control, signal) {
  for (const message of batch.messages) {
    if (!isRunning() || isStale() || signal?.aborted) return;
    if (!message.value || message.value.length > 65536 || !/^\d+$/.test(message.offset)) throw new Error('Invalid trace notification');
    const value = JSON.parse(message.value.toString('utf8'));
    const reply = await control('/notifications/', {deliveries: [{topic: batch.topic, partition: batch.partition,
      // Kafka offsets are 64 bit. DRF IntegerField accepts decimal strings.
      offset: message.offset, value}]}, {signal});
    if (reply.accepted_events + reply.duplicate_events !== 1) throw new Error('Notification was not acknowledged');
    resolveOffset(message.offset);
    await heartbeat();
  }
}

export async function saveReport(spool, claim, result) {
  validateClaim(claim);
  await mkdir(spool, {recursive: true, mode: 0o700});
  const destination = join(spool, claim.attempt_id + '.json');
  const body = JSON.stringify({idempotency_key: claim.attempt_id, lease_token: claim.lease_token, result});
  if (Buffer.byteLength(body) > 2 * 1024 * 1024) throw new Error('Investigation report exceeds publication bound');
  const pending = destination + '.pending';
  const file = await open(pending, 'wx', 0o600);
  try { await file.writeFile(body); await file.sync(); } finally { await file.close(); }
  await rename(pending, destination);
  const directory = await open(spool, 'r');
  try { await directory.sync(); } finally { await directory.close(); }
  return destination;
}

export async function publishSavedReport(path, control, signal) {
  if ((await stat(path)).size > 2 * 1024 * 1024) throw new Error('Invalid saved report size');
  const payload = JSON.parse(await readFile(path, 'utf8'));
  const reply = await control('/reports/', payload, {signal});
  if (!['accepted', 'duplicate'].includes(reply.status)) throw new Error('Report was not durably acknowledged');
  await unlink(path);
  return reply;
}

// Model execution and publication have separate retry boundaries. A finished
// report remains on the mounted spool until Django acknowledges it.
export async function processClaim(claim, {control, investigate, spool, signal, heartbeatMs = 15000}) {
  validateClaim(claim);
  const cancel = new AbortController();
  const runSignal = AbortSignal.any([cancel.signal, AbortSignal.timeout(claim.limits.deadline_seconds * 1000), ...(signal ? [signal] : [])]);
  const scope = Object.fromEntries(['organization_id', 'workspace_id', 'project_id', 'job_id', 'lease_token'].map(key => [key, claim[key]]));
  const heartbeatStop = new AbortController();
  let leaseError;
  const heartbeats = (async () => {
    try {
      while (!heartbeatStop.signal.aborted) {
        await delay(heartbeatMs, null, {signal: heartbeatStop.signal});
        const reply = await control(`/attempts/${claim.attempt_id}/`, {...scope, action: 'renew'}, {method: 'PATCH', signal: runSignal});
        if (reply.cancellation_requested || reply.status !== 'claimed') throw new Error('Investigation lease revoked');
      }
    } catch (error) {
      if (!heartbeatStop.signal.aborted) { leaseError = error; cancel.abort(); }
    }
  })();
  try {
    const result = await investigate(claim, {signal: runSignal});
    // Save even a failed/stale attempt for accounting; Django fences its active projection.
    const path = await saveReport(spool, claim, result);
    if (leaseError) throw leaseError;
    return await publishSavedReport(path, control, signal);
  } finally {
    heartbeatStop.abort();
    await heartbeats;
  }
}

export async function runCoordinator({control, investigate, spool, workerId, engineVersion,
  concurrency = 4, pollMs = 1000, maxSpoolFiles = 1000, signal, onError = () => {}}) {
  if (!Number.isSafeInteger(concurrency) || concurrency < 1 || concurrency > 50) throw new Error('Invalid worker concurrency');
  await mkdir(spool, {recursive: true, mode: 0o700});
  const active = new Map();
  const publishing = new Set();
  try {
    while (!signal.aborted) {
      try {
        // Bounded spool; stop admission if publication is unhealthy. No trace payloads in it.
        const files = (await readdir(spool)).filter(name => /^[a-f0-9-]{36}\.json$/.test(name));
        for (const name of files.slice(0, 50)) {
          const attemptId = name.slice(0, -5);
          if (active.has(attemptId) || publishing.has(name)) continue;
          publishing.add(name);
          try { await publishSavedReport(join(spool, name), control, signal); }
          finally { publishing.delete(name); }
        }
        if (files.length + active.size < maxSpoolFiles && active.size < concurrency) {
          const {claims} = await control('/claims/', {worker_id: workerId, engine_version: engineVersion, limit: concurrency - active.size}, {signal});
          if (!Array.isArray(claims) || claims.length > concurrency - active.size) throw new Error('Invalid claim response');
          for (const claim of claims) {
            if (active.has(claim.attempt_id)) throw new Error('Duplicate claimed attempt');
            const task = processClaim(claim, {control, investigate, spool, signal})
              .catch(error => onError(error, claim.attempt_id)).finally(() => active.delete(claim.attempt_id));
            active.set(claim.attempt_id, task);
          }
        }
      } catch (error) { onError(error); }
      await delay(pollMs, null, {signal});
    }
  } catch (error) { if (!signal.aborted) throw error; }
  finally { await Promise.allSettled(active.values()); }
}
