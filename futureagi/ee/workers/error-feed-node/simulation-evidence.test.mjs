import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {downloadSimulationEvidence, createSimulationEvidenceReader} from './simulation-evidence.mjs';

function claim() {
  return {workload_type: 'simulation_test_execution', contract_version: 'omega-simulation/v1',
    organization_id: randomUUID(), workspace_id: null, project_id: randomUUID(), test_execution_id: randomUUID(),
    job_id: randomUUID(), attempt_id: randomUUID(), generation: 1, engine_version: 'omega-sim-test',
    lease_token: 'test-lease', read_cutoff: new Date().toISOString(),
    memory: {snapshot_id: 'memory-snapshot', digest: 'sha256:test', entries: []},
    limits: {deadline_seconds: 60, max_model_calls: 12, max_children: 2, max_input_tokens_total: 100000,
      max_output_tokens_total: 8000, max_evidence_bytes: 65536, max_tool_result_bytes: 4096}};
}

function call(scenario = 'scenario') {
  return {call_execution_id: randomUUID(), status: 'completed', simulation_call_type: 'conversation', scenario,
    call_summary: 'Recorded response', error_message: null, ended_reason: null,
    transcript: [{id: randomUUID(), speaker: 'user', content: 'Please perform the simulated action.', start_time: 1, end_time: 2},
      {id: randomUUID(), speaker: 'assistant', content: 'Action completed.', start_time: 3, end_time: 4}]};
}

test('simulation evidence pages are complete and issue receipts scoped to call executions', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'omega-simulation-evidence-test-'));
  try {
    const job = claim(), calls = [call('first'), call('second')], requests = [];
    const control = async (path, body) => {
      requests.push({path, body});
      return body.cursor === 0
        ? {calls: calls.slice(0, 1), next_cursor: 1, total_calls: 2}
        : {calls: calls.slice(1), next_cursor: 2, total_calls: 2};
    };
    const store = await downloadSimulationEvidence(job, join(dir, 'calls.jsonl'), {control});
    assert.equal(store.coverage.observed_call_count, 2);
    assert.equal(store.coverage.read_complete, true);
    assert.deepEqual(requests, [0, 1].map(cursor => ({
      path: `/attempts/${job.attempt_id}/simulation-evidence/`, body: {lease_token: job.lease_token, cursor},
    })));

    const reader = createSimulationEvidenceReader(store, {maxResultBytes: 4096, maxTotalBytes: 8192});
    assert.deepEqual(reader.inventory().calls.map(row => row.call_execution_id), calls.map(row => row.call_execution_id));
    assert.equal(reader.allCallsRead(), false);
    for (const row of calls) {
      const evidence = await reader.read(row.call_execution_id, 0, 4096);
      assert.equal(evidence.more, false);
      assert.equal(JSON.parse(evidence.text).call_execution_id, row.call_execution_id);
    }
    assert.equal(reader.allCallsRead(), true);
    assert.deepEqual(reader.receipts().map(({call_execution_id, span_id}) => [call_execution_id, span_id]),
      calls.map(({call_execution_id}) => [call_execution_id, undefined]));
  } finally {
    await rm(dir, {recursive: true, force: true});
  }
});

test('simulation evidence rejects inconsistent pagination, duplicate rows and byte overflow', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'omega-simulation-evidence-test-'));
  const job = claim(), row = call();
  try {
    const malformed = [
      {name: 'short page', pages: [{calls: [], next_cursor: 0, total_calls: 1}]},
      {name: 'bad cursor', pages: [{calls: [row], next_cursor: 0, total_calls: 1}]},
      {name: 'changed total', pages: [{calls: [row], next_cursor: 1, total_calls: 2}, {calls: [], next_cursor: 1, total_calls: 1}]},
      {name: 'duplicate identity', pages: [{calls: [row], next_cursor: 1, total_calls: 2}, {calls: [row], next_cursor: 2, total_calls: 2}]},
    ];
    for (const [index, scenario] of malformed.entries()) {
      let page = 0;
      const control = async () => scenario.pages[page++];
      await assert.rejects(downloadSimulationEvidence(job, join(dir, `bad-${index}.jsonl`), {control}), /simulation|page|cursor/i);
    }
    await assert.rejects(downloadSimulationEvidence(job, join(dir, 'oversize.jsonl'), {
      control: async () => ({calls: [row], next_cursor: 1, total_calls: 1}), maxBytes: 1,
    }), /byte limit/i);
  } finally {
    await rm(dir, {recursive: true, force: true});
  }
});
