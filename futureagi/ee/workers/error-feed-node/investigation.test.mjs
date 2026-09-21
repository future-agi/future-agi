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

for (const scenario of ['controller_truncated', 'controller_exhausted', 'verifier_truncated', 'provider_overrun']) {
  test(`output budget reserves verification and rejects partial assessments: ${scenario}`, async () => {
    const scratch = await mkdtemp(join(tmpdir(), 'omega-budget-test-'));
    try {
      const claim = makeClaim();
      claim.limits.max_output_tokens_total = 1000;
      const row = {id: 'span-budget', project_id: claim.project_id, trace_id: claim.trace_id,
        input: 'Refund 10', output: 'Refunded 10'};
      const raw = JSON.stringify(row);
      const evidenceId = `${row.id}:0:${Buffer.byteLength(raw)}`;
      const assessment = {outcome: 'success', findings: [], requirement_checks: [
        {requirement_id: 'refund', requirement: 'Refund 10', status: 'satisfied', evidence_ids: [evidenceId]}]};
      const caps = [], phases = [];
      const result = await investigateTrace(claim, {scratchRoot: scratch,
        fetchEvidence: async (c, path) => storeEvidence([Buffer.from(raw + '\n')], path, c),
        gatewayConfig: {baseUrl: 'http://fixture/v1', model: 'fixture', apiKey: 'fixture',
          fetchImpl: async (_url, init) => {
            const request = JSON.parse(init.body);
            const verifier = request.messages.find(m => m.role === 'system').content.includes('Independently check');
            phases.push(verifier ? 'verifier' : 'controller');
            caps.push(request.max_tokens);
            let message, used, finishReason = 'stop';
            if (caps.length === 1) {
              used = 100;
              message = {role: 'assistant', content: '', tool_calls: [{id: 'read-budget', type: 'function',
                function: {name: 'read_span', arguments: JSON.stringify({span_id: row.id, offset: 0, length: 4096})}}]};
            } else if (!verifier) {
              used = 400;
              finishReason = scenario === 'controller_exhausted' ? 'stop' : 'length';
              message = {role: 'assistant', content: finishReason === 'length' ? '{"action":' : JSON.stringify({
                action: 'investigate', question: 'Check refund', child_instructions: 'Compare amount', assessment})};
            } else {
              used = scenario === 'provider_overrun' ? 501 : 300;
              finishReason = scenario === 'verifier_truncated' ? 'length' : 'stop';
              message = {role: 'assistant', content: JSON.stringify(assessment)};
            }
            return new Response(JSON.stringify({choices: [{message, finish_reason: finishReason}],
              usage: {prompt_tokens: 100, completion_tokens: used}}),
            {headers: {'content-type': 'application/json', 'x-agentcc-cost': '0.000100'}});
          }}});
      assert.deepEqual(caps, [500, 400, 500]);
      assert.deepEqual(phases, ['controller', 'controller', 'verifier']);
      assert.equal(result.usage.model_calls, 3);
      assert.equal(result.usage.cost_usd, 0.0003);
      const failed = ['verifier_truncated', 'provider_overrun'].includes(scenario);
      assert.equal(result.execution_status, failed ? 'failed' : 'completed');
      assert.equal(result.outcome, failed ? 'unknown' : 'success');
      if (!failed) assert.equal(result.coverage.read_complete, true);
      assert.equal(result.usage.output_tokens, scenario === 'provider_overrun' ? 1001 : 800);
      assert.deepEqual(await readdir(scratch), []);
    } finally { await rm(scratch, {recursive: true, force: true}); }
  });
}

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

test('unread contradictory child cannot produce supported success', async () => {
  const scratch = await mkdtemp(join(tmpdir(), 'omega-unread-child-test-'));
  try {
    const claim = makeClaim();
    const root = {id: 'root-span', project_id: claim.project_id, trace_id: claim.trace_id,
      parent_span_id: '', input: 'Refund 10', output: 'Refund complete'};
    const child = {id: 'child-span', project_id: claim.project_id, trace_id: claim.trace_id,
      parent_span_id: root.id, input: 'Refund 10', output: 'Refunded 5'};
    const rootRaw = JSON.stringify(root);
    const rows = rootRaw + '\n' + JSON.stringify(child) + '\n';
    const evidenceId = `${root.id}:0:${Buffer.byteLength(rootRaw)}`;
    const assessment = {outcome: 'success', findings: [], requirement_checks: [
      {requirement_id: 'refund', requirement: 'Refund 10', status: 'satisfied', evidence_ids: [evidenceId]}]};
    const result = await investigateTrace(claim, {scratchRoot: scratch,
      fetchEvidence: async (c, path) => storeEvidence([Buffer.from(rows)], path, c),
      gatewayConfig: {baseUrl: 'http://fixture/v1', model: 'fixture', apiKey: 'fixture',
        fetchImpl: async (_url, init) => {
          const request = JSON.parse(init.body);
          const verifier = request.messages.find(m => m.role === 'system').content.includes('Independently check');
          const hasToolResponse = request.messages.some(m => m.role === 'tool');
          const message = !verifier && !hasToolResponse
            ? {role: 'assistant', content: '', tool_calls: [{id: 'read-root', type: 'function',
              function: {name: 'read_span', arguments: JSON.stringify({span_id: root.id, offset: 0, length: 4096})}}]}
            : {role: 'assistant', content: JSON.stringify(verifier ? assessment :
              {action: 'finish', question: '', child_instructions: '', assessment})};
          return new Response(JSON.stringify({choices: [{message}],
            usage: {prompt_tokens: 10, completion_tokens: 5}}),
          {headers: {'content-type': 'application/json', 'x-agentcc-cost': '0'}});
        }}});
    assert.equal(result.coverage.observed_span_count, 2);
    assert.equal(result.coverage.read_complete, false);
    assert.equal(result.evidence_receipts.length, 1);
    assert.equal(result.outcome, 'unknown');
  } finally { await rm(scratch, {recursive: true, force: true}); }
});

test('verifier rechecks an unread child after an unsupported success', async () => {
  const scratch = await mkdtemp(join(tmpdir(), 'omega-cover-loop-test-'));
  try {
    const claim = makeClaim();
    const root = {id: 'root-span', project_id: claim.project_id, trace_id: claim.trace_id,
      parent_span_id: '', input: 'Refund 10', output: 'Refund complete'};
    const child = {id: 'child-span', project_id: claim.project_id, trace_id: claim.trace_id,
      parent_span_id: root.id, input: 'Refund 10', output: 'Refunded 5'};
    const rootRaw = JSON.stringify(root);
    const childRaw = JSON.stringify(child);
    const rootEvidenceId = `${root.id}:0:${Buffer.byteLength(rootRaw)}`;
    const success = {outcome: 'success', findings: [], requirement_checks: [
      {requirement_id: 'refund', requirement: 'Refund 10', status: 'satisfied', evidence_ids: [rootEvidenceId]}]};
    const failure = {outcome: 'failure', findings: [], requirement_checks: [
      {requirement_id: 'refund', requirement: 'Refund 10', status: 'violated', evidence_ids: [rootEvidenceId]}]};
    let verifierCalls = 0;
    const result = await investigateTrace(claim, {scratchRoot: scratch,
      fetchEvidence: async (c, path) => storeEvidence([Buffer.from(rootRaw + '\n' + childRaw + '\n')], path, c),
      gatewayConfig: {baseUrl: 'http://fixture/v1', model: 'fixture', apiKey: 'fixture',
        fetchImpl: async (_url, init) => {
          const request = JSON.parse(init.body);
          const verifier = request.messages.find(m => m.role === 'system').content.includes('Independently check');
          let message;
          if (verifier) {
            verifierCalls++;
            if (verifierCalls === 2) {
              message = {role: 'assistant', content: '', tool_calls: [{id: 'read-child-' + verifierCalls,
                type: 'function', function: {name: 'read_span', arguments: JSON.stringify({span_id: child.id,
                  offset: 0, length: 4096})}}]};
            } else message = {role: 'assistant', content: JSON.stringify(verifierCalls === 1 ? success : failure)};
          } else if (!request.messages.some(m => m.role === 'tool')) {
            message = {role: 'assistant', content: '', tool_calls: [{id: 'read-root', type: 'function',
              function: {name: 'read_span', arguments: JSON.stringify({span_id: root.id, offset: 0, length: 4096})}}]};
          } else message = {role: 'assistant', content: JSON.stringify({action: 'finish', question: '',
            child_instructions: '', assessment: success})};
          return new Response(JSON.stringify({choices: [{message}], usage: {prompt_tokens: 10, completion_tokens: 5}}),
            {headers: {'content-type': 'application/json', 'x-agentcc-cost': '0'}});
        }}});
    assert.equal(verifierCalls, 3);
    assert.equal(result.execution_status, 'completed');
    assert.equal(result.outcome, 'failure');
    assert.equal(result.coverage.read_complete, true);
  } finally { await rm(scratch, {recursive: true, force: true}); }
});

test('question-driven audio inspection runs inside the investigator and keeps the existing report contract', async () => {
  const scratch = await mkdtemp(join(tmpdir(), 'omega-audio-investigation-test-'));
  try {
    const claim = makeClaim();
    const row = {id: 'audio-span', project_id: claim.project_id, trace_id: claim.trace_id,
      parent_span_id: '', input: 'Do not interrupt the caller', span_attr_str: {
        'gen_ai.voice.recording.url': 'https://media.example.test/call.wav'}};
    const raw = JSON.stringify(row);
    const evidenceId = `${row.id}:0:${Buffer.byteLength(raw)}`;
    const assessment = {outcome: 'success', findings: [], requirement_checks: [{requirement_id: 'interruption',
      requirement: 'Do not interrupt the caller', status: 'satisfied', evidence_ids: [evidenceId]}]};
    let controllerStep = 0, audioRequests = 0, resolutions = 0;
    const result = await investigateTrace(claim, {scratchRoot: scratch,
      resolveRecording: async ({store, span_id}) => {
        assert.equal(store.index.has(span_id), true);
        resolutions++;
        return {url: 'https://media.example.test/call.wav', format: 'wav'};
      },
      fetchEvidence: async (c, path) => storeEvidence([Buffer.from(raw + '\n')], path, c),
      gatewayConfig: {baseUrl: 'https://gateway.invalid/v1', model: 'fixture', apiKey: 'fixture',
        fetchImpl: async (_url, init) => {
          const request = JSON.parse(init.body);
          let message;
          if (Array.isArray(request.messages[0]?.content)) {
            audioRequests++;
            assert.equal(request.messages[0].content[1].file.file_id, 'https://media.example.test/call.wav');
            message = {role: 'assistant', content: JSON.stringify({answer: 'No interruption',
              observations: [{statement: 'Caller finished the sentence before the agent replied',
                start_seconds: 2, end_seconds: 5, speaker: 'agent', confidence: 0.8}],
              metrics: [{name: 'assistant_interruptions', value: 0, unit: 'count', method: 'audio review'}],
              uncertainty: null})};
          } else if (request.messages.find(m => m.role === 'system').content.includes('Independently check')) {
            message = {role: 'assistant', content: JSON.stringify(assessment)};
          } else if (controllerStep++ === 0) {
            message = {role: 'assistant', content: '', tool_calls: [{id: 'read-audio-span', type: 'function',
              function: {name: 'read_span', arguments: JSON.stringify({span_id: row.id, offset: 0, length: 4096})}}]};
          } else if (controllerStep === 2) {
            message = {role: 'assistant', content: '', tool_calls: [{id: 'inspect-audio', type: 'function',
              function: {name: 'inspect_audio', arguments: JSON.stringify({span_id: row.id,
                question: 'Did the assistant interrupt the caller?'})}}]};
          } else {
            message = {role: 'assistant', content: JSON.stringify({action: 'finish', question: '',
              child_instructions: '', assessment})};
          }
          return new Response(JSON.stringify({choices: [{message}],
            usage: {prompt_tokens: 20, completion_tokens: 20}}),
          {headers: {'content-type': 'application/json', 'x-agentcc-cost': '0.000100',
            'x-request-id': `gateway-${controllerStep}-${audioRequests}`}});
        }}});
    assert.equal(result.execution_status, 'completed');
    assert.equal(result.outcome, 'success');
    assert.equal(resolutions, 1);
    assert.equal(audioRequests, 1);
    assert.equal(result.usage.model_calls, 5);
    assert.equal(result.gateway_accounting.length, 5);
    assert.equal(result.usage.cost_usd, 0.0005);
    assert.equal(result.evidence_receipts.length, 1);
    assert.equal(result.evidence_receipts[0].evidence_id, evidenceId);
    assert.equal('recording_digest' in result.evidence_receipts[0], false);
  } finally {await rm(scratch, {recursive: true, force: true});}
});
