import {open} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {validateClaim} from './evidence-store.mjs';

const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
const pageBytesLimit = 2 * 1024 * 1024;
const maxCalls = 50000;
const callKeys = ['call_execution_id', 'status', 'simulation_call_type', 'scenario', 'call_summary', 'error_message', 'ended_reason', 'transcript'];
const transcriptKeys = ['id', 'speaker', 'content', 'start_time', 'end_time'];
const evaluationKeys = ['name', 'value', 'passed', 'reason'];
const goalKeys = ['use_case', 'sub_goals', 'expected_outcome'];
// Optional so this worker accepts pages from platforms that predate them.
const optionalCallKeys = ['evaluations', 'goals'];

function exactKeys(value, keys, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
      || Object.keys(value).length !== keys.length || keys.some(key => !Object.hasOwn(value, key))) {
    throw new Error('Invalid ' + label + ' shape');
  }
}

function requiredText(value, maxLength, label) {
  if (typeof value !== 'string' || !value.trim() || value.length > maxLength) throw new Error('Invalid ' + label);
}

function optionalText(value, maxLength, label) {
  if (value !== null && (typeof value !== 'string' || value.length > maxLength)) throw new Error('Invalid ' + label);
}

function validTime(value) {
  return value === null || (typeof value === 'number' && Number.isFinite(value) && value >= 0);
}

function validateEvaluations(evaluations) {
  if (!Array.isArray(evaluations) || evaluations.length > 200) throw new Error('Invalid simulation evaluations');
  for (const item of evaluations) {
    exactKeys(item, evaluationKeys, 'simulation evaluation');
    requiredText(item.name, 256, 'simulation evaluation name');
    optionalText(item.value, 8000, 'simulation evaluation value');
    if (item.passed !== null && typeof item.passed !== 'boolean') throw new Error('Invalid simulation evaluation verdict');
    optionalText(item.reason, pageBytesLimit, 'simulation evaluation reason');
  }
}

function validateGoals(goals) {
  if (goals === null) return;
  exactKeys(goals, goalKeys, 'simulation goals');
  optionalText(goals.use_case, pageBytesLimit, 'simulation use case');
  optionalText(goals.expected_outcome, pageBytesLimit, 'simulation expected outcome');
  if (!Array.isArray(goals.sub_goals) || goals.sub_goals.length > 100) throw new Error('Invalid simulation sub-goals');
  for (const goal of goals.sub_goals) requiredText(goal, 256, 'simulation sub-goal');
}

function validateCall(call) {
  exactKeys(call, [...callKeys, ...optionalCallKeys.filter(key => Object.hasOwn(call ?? {}, key))],
    'simulation call');
  if (Object.hasOwn(call, 'evaluations')) validateEvaluations(call.evaluations);
  if (Object.hasOwn(call, 'goals')) validateGoals(call.goals);
  if (!uuid.test(call.call_execution_id ?? '')) throw new Error('Invalid simulation call identity');
  requiredText(call.status, 64, 'simulation call status');
  requiredText(call.simulation_call_type, 128, 'simulation call type');
  requiredText(call.scenario, pageBytesLimit, 'simulation scenario');
  optionalText(call.call_summary, pageBytesLimit, 'simulation call summary');
  optionalText(call.error_message, pageBytesLimit, 'simulation call error');
  optionalText(call.ended_reason, pageBytesLimit, 'simulation call end reason');
  if (!Array.isArray(call.transcript)) throw new Error('Invalid simulation transcript');
  const transcriptIds = new Set();
  for (const item of call.transcript) {
    exactKeys(item, transcriptKeys, 'simulation transcript entry');
    if (!uuid.test(item.id ?? '') || transcriptIds.has(item.id)) throw new Error('Invalid simulation transcript identity');
    transcriptIds.add(item.id);
    requiredText(item.speaker, 128, 'simulation transcript speaker');
    if (typeof item.content !== 'string' || item.content.length > pageBytesLimit) throw new Error('Invalid simulation transcript content');
    if (!validTime(item.start_time) || !validTime(item.end_time)) throw new Error('Invalid simulation transcript time');
  }
}

export async function downloadSimulationEvidence(claim, path, options = {}) {
  validateClaim(claim);
  if (claim.workload_type !== 'simulation_test_execution') throw new Error('Simulation evidence requires a simulation claim');
  const {control, signal, maxBytes = claim.limits.max_evidence_bytes} = options;
  if (typeof control !== 'function' || !Number.isSafeInteger(maxBytes) || maxBytes < 1) throw new Error('Invalid simulation evidence reader');
  const evidenceByteLimit = Math.min(maxBytes, 256 * 1024 * 1024);

  const file = await open(path, 'wx', 0o600);
  const digest = createHash('sha256');
  const index = new Map();
  let bytes = 0, cursor = 0, totalCalls;
  try {
    do {
      signal?.throwIfAborted();
      const page = await control(`/attempts/${claim.attempt_id}/simulation-evidence/`,
        {lease_token: claim.lease_token, cursor}, {signal});
      if (Buffer.byteLength(JSON.stringify(page)) > pageBytesLimit) throw new Error('Simulation evidence page exceeded control limit');
      exactKeys(page, ['calls', 'next_cursor', 'total_calls'], 'simulation evidence page');
      if (!Array.isArray(page.calls) || !Number.isSafeInteger(page.total_calls) || page.total_calls < 0
          || page.total_calls > maxCalls || !Number.isSafeInteger(page.next_cursor)) throw new Error('Invalid simulation evidence page');
      if (totalCalls === undefined) totalCalls = page.total_calls;
      if (page.total_calls !== totalCalls || cursor > totalCalls || page.next_cursor !== cursor + page.calls.length
          || page.next_cursor > totalCalls || (cursor < totalCalls && page.calls.length === 0)) {
        throw new Error('Incomplete or inconsistent simulation evidence page');
      }
      for (const call of page.calls) {
        signal?.throwIfAborted();
        validateCall(call);
        if (index.has(call.call_execution_id)) throw new Error('Duplicate simulation call identity');
        const line = Buffer.from(JSON.stringify(call) + '\n');
        if (bytes + line.length > evidenceByteLimit) throw new Error('Simulation evidence byte limit exceeded');
        let written = 0;
        while (written < line.length) written += (await file.write(line, written, line.length - written)).bytesWritten;
        digest.update(line);
        const offset = bytes;
        const {call_execution_id, status, simulation_call_type} = call;
        index.set(call_execution_id, {call_execution_id, status, simulation_call_type, offset, bytes: line.length - 1});
        bytes += line.length;
      }
      cursor = page.next_cursor;
    } while (cursor < totalCalls);
    if (cursor !== totalCalls || index.size !== totalCalls) throw new Error('Simulation evidence did not cover the execution');
    await file.sync();
    return {path, bytes, digest: 'sha256:' + digest.digest('hex'), index,
      coverage: {scope: 'simulation_test_execution', observed_call_count: index.size, read_complete: true}};
  } finally {
    await file.close();
  }
}

export function createSimulationEvidenceReader(store, {maxResultBytes, maxTotalBytes, signal}) {
  const receipts = new Map();
  const readRanges = new Map();
  let readBytes = 0;
  function fullyRead(call) {
    const ranges = (readRanges.get(call.call_execution_id) ?? []).sort((a, b) => a[0] - b[0]);
    let covered = 0;
    for (const [start, end] of ranges) {
      if (start > covered) break;
      covered = Math.max(covered, end);
    }
    return covered >= call.bytes;
  }
  return {
    allCallsRead() {
      for (const call of store.index.values()) if (!fullyRead(call)) return false;
      return true;
    },
    unreadCallIds(limit = 20) {
      const ids = [];
      for (const call of store.index.values()) {
        if (!fullyRead(call)) ids.push(call.call_execution_id);
        if (ids.length >= limit) break;
      }
      return ids;
    },
    inventory(cursor = 0) {
      if (!Number.isSafeInteger(cursor) || cursor < 0 || cursor > store.index.size) throw new Error('Invalid call inventory cursor');
      const calls = [];
      for (const {offset, ...row} of [...store.index.values()].slice(cursor, cursor + 20)) {
        if (Buffer.byteLength(JSON.stringify([...calls, row])) + 128 > maxResultBytes) {
          if (!calls.length) throw new Error('Call inventory row exceeds tool result budget');
          break;
        }
        calls.push(row);
      }
      return {calls, next_cursor: cursor + calls.length, more: cursor + calls.length < store.index.size};
    },
    async read(callExecutionId, offset, length) {
      signal?.throwIfAborted();
      const call = store.index.get(callExecutionId);
      if (!call || !Number.isSafeInteger(offset) || offset < 0 || offset >= call.bytes
          || !Number.isSafeInteger(length) || length < 1 || length > maxResultBytes) throw new Error('Invalid simulation call range');
      const size = Math.min(length, call.bytes - offset, maxResultBytes - 256);
      if (size < 1) throw new Error('Tool result budget is too small');
      if (readBytes + size > maxTotalBytes) throw new Error('Evidence tool budget exhausted');
      readBytes += size;
      const file = await open(store.path, 'r');
      try {
        const buffer = Buffer.alloc(size);
        let read = 0;
        while (read < size) {
          const got = (await file.read(buffer, read, size - read, call.offset + offset + read)).bytesRead;
          if (!got) throw new Error('Simulation evidence file changed');
          read += got;
        }
        let returned = size, result;
        for (;;) {
          let text;
          for (let trim = 0; trim <= 3; trim++) {
            try {
              text = new TextDecoder('utf-8', {fatal: true}).decode(buffer.subarray(0, returned - trim));
              returned -= trim;
              break;
            } catch { /* The last code point may cross the requested boundary. */ }
          }
          if (text === undefined || returned < 1) throw new Error('Read offset splits a UTF-8 character');
          result = {evidence_id: `${callExecutionId}:${offset}:${returned}`, text,
            next_offset: offset + returned, more: offset + returned < call.bytes};
          const serializedBytes = Buffer.byteLength(JSON.stringify(result));
          if (serializedBytes <= maxResultBytes) break;
          returned = Math.floor(returned * maxResultBytes / serializedBytes) - 1;
          if (returned < 1) throw new Error('Tool result budget is too small');
        }
        readBytes -= size - returned;
        receipts.set(result.evidence_id, {evidence_id: result.evidence_id,
          call_execution_id: callExecutionId, excerpt: result.text.slice(0, 8000)});
        const ranges = readRanges.get(callExecutionId) ?? [];
        ranges.push([offset, offset + returned]);
        readRanges.set(callExecutionId, ranges);
        return result;
      } finally {
        await file.close();
      }
    },
    receipts: () => [...receipts.values()],
  };
}
