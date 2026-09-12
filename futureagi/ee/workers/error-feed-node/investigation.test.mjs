import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, rm, readdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {investigateTrace, validateAssessment, canonicalDigest, failureDiagnostic} from './investigation.mjs';
import {storeEvidence} from './evidence-store.mjs';

function makeClaim() {
  return {organization_id: randomUUID(), workspace_id: null, project_id: randomUUID(), trace_id: randomUUID(),
    job_id: randomUUID(), attempt_id: randomUUID(), generation: 1, feature_enabled: true, engine_version: 'omega-file-v1',
    contract_version: 'omega-investigation/v1', read_cutoff: '2026-09-12T10:20:30.123456Z',
    memory: {snapshot_id: 'empty', digest: canonicalDigest([]), entries: []},
    limits: {deadline_seconds: 60, max_model_calls: 12, max_children: 2,
      max_input_tokens_total: 100000, max_output_tokens_total: 8000, max_evidence_bytes: 65536, max_tool_result_bytes: 4096}};
}

test('failure diagnostics classify host budget errors without exposing upstream content', () => {
  assert.equal(failureDiagnostic(new Error('Input context budget exhausted'), 'verifier', 'attempt').reason,
    'input_budget_exhausted');
  const diagnostic = failureDiagnostic(new Error('upstream secret: private-key'), 'controller', 'attempt');
  assert.equal(diagnostic.reason, 'runtime_or_output_validation');
  assert.ok(!JSON.stringify(diagnostic).includes('private-key'));
  for (const [message, code] of [
    ['Structured output failed validation: private-key', 'structured_output_invalid'],
    ['Structured output expected JSON, but parsing failed: private-key', 'structured_output_unparseable'],
    ['Structured output expected JSON, but the model returned empty content.', 'structured_output_empty'],
  ]) {
    const result = failureDiagnostic(new Error(message), 'verifier', 'attempt');
    assert.equal(result.reason, code);
    assert.ok(!JSON.stringify(result).includes('private-key'));
  }
});

async function runScriptedAssessment(rowFields, assessmentForEvidence) {
  const scratch = await mkdtemp(join(tmpdir(), 'omega-coverage-test-'));
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error('Referenced payload URL must not be fetched'); };
  try {
    const claim = makeClaim();
    const row = {id: '1234567890abcdef', project_id: claim.project_id, trace_id: claim.trace_id,
      parent_span_id: '', name: 'externally-backed-operation', ...rowFields};
    const raw = JSON.stringify(row);
    const evidenceId = `${row.id}:0:${Buffer.byteLength(raw)}`;
    const assessment = assessmentForEvidence(evidenceId, row.id);
    const result = await investigateTrace(claim, {scratchRoot: scratch,
      fetchEvidence: async (c, path) => storeEvidence([Buffer.from(raw + '\n')], path, c),
      gatewayConfig: {baseUrl: 'http://fixture/v1', model: 'fixture-model', apiKey: 'fixture-secret',
        fetchImpl: async (_url, init) => {
          const request = JSON.parse(init.body);
          const system = request.messages.find(message => message.role === 'system').content;
          const phase = system.includes('Independently check') ? 'verifier' : 'controller';
          const shared = JSON.parse(request.messages.find(message => message.role === 'user').content);
          assert.equal(shared.coverage.read_complete, false);
          assert.deepEqual(shared.inventory.spans[0].unresolved_external_payloads,
            [rowFields.input_gcs_url ? 'input_gcs_url' : 'output_gcs_url']);
          assert.ok(shared.unavailable.includes('external_payload_resolution'));
          const toolMessage = request.messages.find(message => message.role === 'tool');
          const message = !toolMessage
            ? {role: 'assistant', content: '', tool_calls: [{id: 'read-' + phase, type: 'function',
              function: {name: 'read_span', arguments: JSON.stringify({span_id: row.id, offset: 0, length: 4096})}}]}
            : {role: 'assistant', content: JSON.stringify(phase === 'controller'
              ? {action: 'finish', question: '', child_instructions: '', assessment} : assessment)};
          return new Response(JSON.stringify({choices: [{message}], usage: {prompt_tokens: 10, completion_tokens: 5}}),
            {headers: {'Content-Type': 'application/json', 'x-agentcc-cost': '0'}});
        }}});
    return {result, raw};
  } finally {
    globalThis.fetch = originalFetch;
    await rm(scratch, {recursive: true, force: true});
  }
}

test('real Omega controller, child, return and verifier use file tools and preserve gateway cost', async () => {
  const scratch = await mkdtemp(join(tmpdir(), 'omega-graph-test-'));
  try {
    const claim = makeClaim();
    // The current-stack budget supports this seven-call path when the guard
    // counts the serialized gateway body instead of duplicated agent metadata.
    claim.limits.max_model_calls = 8;
    claim.limits.max_input_tokens_total = 20000;
    const row = {id: '1234567890abcdef', project_id: claim.project_id, trace_id: claim.trace_id,
      parent_span_id: '', name: 'refund', input: 'Refund exactly 10', output: 'Refunded 5'};
    const raw = JSON.stringify(row);
    const evidenceId = `${row.id}:0:${Buffer.byteLength(raw)}`;
    const unknownRole = {status: 'unknown', span_id: null, evidence_ids: []};
    const assessment = {outcome: 'failure', requirement_checks: [{requirement_id: 'refund-amount',
      requirement: 'Refund 10', status: 'violated', evidence_ids: [evidenceId]}],
      findings: [{finding_id: 'f1', kind: 'incorrect refund amount', statement: 'Only 5 refunded instead of 10',
        requirement_id: 'refund-amount', recovery: 'not_recovered', evidence_ids: [evidenceId],
        attribution: {origin: {status: 'supported', span_id: row.id, evidence_ids: [evidenceId]}, decisive: unknownRole, symptom: unknownRole}}]};
    const phases = [];
    const result = await investigateTrace(claim, {scratchRoot: scratch,
      fetchEvidence: async (c, path) => storeEvidence([Buffer.from(raw + '\n')], path, c),
      gatewayConfig: {baseUrl: 'http://fixture/v1', model: 'fixture-model', apiKey: 'fixture-secret',
        fetchImpl: async (_url, init) => {
          const request = JSON.parse(init.body);
          const system = request.messages.find(message => message.role === 'system').content;
          const phase = system.includes('Independently check') ? 'verifier' : system.includes('Focused assignment') ? 'child' : 'controller';
          phases.push(phase);
          const toolMessage = request.messages.find(message => message.role === 'tool');
          const hasChildren = request.messages.some(message => message.role === 'user' && message.content.includes('"question":"Check refund amount"'));
          let message;
          if (!toolMessage && !(phase === 'controller' && hasChildren)) message = {role: 'assistant', content: '',
            tool_calls: [{id: 'read-' + phases.length, type: 'function', function: {name: 'read_span', arguments: JSON.stringify({span_id: row.id, offset: 0, length: 4096})}}]};
          else message = {role: 'assistant', content: JSON.stringify(phase === 'controller'
            ? {action: hasChildren ? 'finish' : 'investigate', question: 'Check refund amount', child_instructions: 'Compare requested and refunded amounts.', assessment}
            : assessment)};
          return new Response(JSON.stringify({choices: [{message}], usage: {prompt_tokens: 100, completion_tokens: 50}}),
            {headers: {'Content-Type': 'application/json', 'x-agentcc-cost': '0.000100'}});
        }}});
    assert.equal(result.execution_status, 'completed');
    assert.equal(result.outcome, 'failure');
    assert.deepEqual(phases, ['controller', 'controller', 'child', 'child', 'controller', 'verifier', 'verifier']);
    assert.equal(result.usage.model_calls, 7);
    assert.equal(result.usage.cost_usd, 0.0007);
    assert.equal(result.evidence_receipts[0].excerpt, raw);
    assert.equal(result.findings[0].attribution.origin.span_id, row.id);
    assert.deepEqual(await readdir(scratch), []);
    const {result_digest, ...body} = result;
    assert.equal(result_digest, canonicalDigest(body));
    assert.ok(!JSON.stringify(result).includes('fixture-secret'));
  } finally { await rm(scratch, {recursive: true, force: true}); }
});

test('unobserved citations, unsupported success and guessed origin cannot publish', () => {
  assert.throws(() => validateAssessment({outcome: 'success', findings: [], requirement_checks: []}, []));
  assert.throws(() => validateAssessment({outcome: 'failure', findings: [], requirement_checks: [
    {requirement_id: 'r1', status: 'violated', evidence_ids: ['invented']}]}, []), /Unobserved/);
  const item = {finding_id: 'f1', requirement_id: null, evidence_ids: ['e1'], attribution: {
    origin: {status: 'supported', span_id: 'wrong', evidence_ids: ['e1']}}};
  assert.throws(() => validateAssessment({outcome: 'unknown', findings: [item], requirement_checks: []}, [{evidence_id: 'e1', span_id: 'right'}]), /Unsupported attributed/);
  assert.throws(() => validateAssessment({outcome: 'success', findings: [], requirement_checks: [
    {requirement_id: 'r1', status: 'satisfied', evidence_ids: ['e1']}],
  }, [{evidence_id: 'e1', span_id: 'right'}], {read_complete: false}), /incomplete evidence coverage/);
});

test('Node report digest matches the Django wire fixture', () => {
  assert.equal(canonicalDigest({read_cutoff: '2026-09-12T10:20:30.123456Z',
    gateway_accounting: [{cost: null, model_used: 'openai/test', raw: {charged: false}},
      {cost: 0, model_used: 'openai/test', raw: {units: 1}}, {cost: 1, model_used: 'openai/test', raw: null}],
    nested: {small: 0.001, large: 1000000000000000}}),
  'sha256:c0c2d86a98393d4a34d1ec1f7aabff4b5c134d06c7fb568185f607bbb91e09c8');
});

test('unresolved external payloads downgrade attempted success without failing the investigation', async () => {
  const {result} = await runScriptedAssessment(
    {input: 'Submit refund', output: 'Refund accepted', input_gcs_url: 'gs://tenant/input'},
    evidenceId => ({outcome: 'success', findings: [], requirement_checks: [
      {requirement_id: 'refund', requirement: 'Submit refund', status: 'satisfied', evidence_ids: [evidenceId]}]}));

  assert.equal(result.execution_status, 'completed');
  assert.equal(result.outcome, 'unknown');
  assert.equal(result.coverage.read_complete, false);
  assert.equal(result.requirement_checks[0].status, 'satisfied');
  assert.equal(result.evidence_receipts.length, 1);
});

test('unresolved external payloads preserve findings supported by inline evidence', async () => {
  const unknownRole = {status: 'unknown', span_id: null, evidence_ids: []};
  const {result, raw} = await runScriptedAssessment(
    {input: 'Refund 10', output: 'Refunded 5', output_gcs_url: 'https://untrusted.invalid/output'},
    (evidenceId, spanId) => ({outcome: 'failure', requirement_checks: [{requirement_id: 'refund',
      requirement: 'Refund 10', status: 'violated', evidence_ids: [evidenceId]}],
    findings: [{finding_id: 'f1', kind: 'incorrect refund amount', statement: 'Only 5 was refunded',
      requirement_id: 'refund', evidence_ids: [evidenceId], recovery: 'not_recovered',
      attribution: {origin: {status: 'supported', span_id: spanId, evidence_ids: [evidenceId]},
        decisive: unknownRole, symptom: unknownRole}}]}));

  assert.equal(result.execution_status, 'completed');
  assert.equal(result.outcome, 'failure');
  assert.equal(result.coverage.read_complete, false);
  assert.equal(result.findings.length, 1);
  assert.equal(result.evidence_receipts[0].excerpt, raw);
});
