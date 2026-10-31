import {configureObservability, stopObservability} from './observability.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, rm, readdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {investigateTrace, investigateSimulation, validateAssessment, canonicalDigest, failureDiagnostic, deriveOutcome} from './investigation.mjs';
import {storeEvidence, EvidenceReadError} from './evidence-store.mjs';

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
    ['Gateway request aborted', 'gateway_request_aborted'],
    ['Gateway transport failed', 'gateway_transport_failed'],
    ['Gateway response could not be processed', 'gateway_response_invalid'],
    ['ClickHouse evidence read failed', 'clickhouse_read_failed'],
  ]) {
    const result = failureDiagnostic(new Error(message), 'verifier', 'attempt');
    assert.equal(result.reason, code);
    assert.ok(!JSON.stringify(result).includes('private-key'));
  }
});

for (const malformed of ['{', '{"action":"finish"}']) {
  test(`controller repairs one malformed structured response: ${malformed}`, async () => {
    const scratch = await mkdtemp(join(tmpdir(), 'omega-structured-repair-test-'));
    try {
      const claim = makeClaim();
      const row = {id: 'span-repair', project_id: claim.project_id, trace_id: claim.trace_id,
        input: 'Check the trace', output: 'Outcome unresolved'};
      const assessment = {outcome: 'unknown', findings: [], requirement_checks: []};
      const calls = [];
      const result = await investigateTrace(claim, {scratchRoot: scratch,
        fetchEvidence: async (c, path) => storeEvidence([Buffer.from(JSON.stringify(row) + '\n')], path, c),
        gatewayConfig: {baseUrl: 'http://fixture/v1', model: 'fixture', apiKey: 'fixture',
          fetchImpl: async (_url, init) => {
            const request = JSON.parse(init.body);
            const system = request.messages.find(message => message.role === 'system').content;
            const verifier = system.includes('Independently check');
            const repairing = request.messages.some(message => typeof message.content === 'string'
              && message.content.includes('response_format_retry'));
            calls.push({verifier, repairing});
            const content = verifier ? JSON.stringify(assessment) : repairing
              ? JSON.stringify({action: 'finish', question: '', child_instructions: '', assessment}) : malformed;
            return new Response(JSON.stringify({choices: [{message: {role: 'assistant', content},
              finish_reason: 'stop'}], usage: {prompt_tokens: 10, completion_tokens: 10}}),
            {headers: {'content-type': 'application/json', 'x-agentcc-cost': '0.000100'}});
          }}});
      assert.equal(result.execution_status, 'completed');
      assert.ok(!('error_message' in result));
      assert.equal(result.outcome, 'unknown');
      assert.deepEqual(calls, [{verifier: false, repairing: false},
        {verifier: false, repairing: true}, {verifier: true, repairing: false}]);
      assert.equal(result.usage.model_calls, 3);
    } finally { await rm(scratch, {recursive: true, force: true}); }
  });
}

test('a trace investigation hands its control client to the evidence read', async () => {
  const scratch = await mkdtemp(join(tmpdir(), 'omega-conversation-evidence-test-'));
  try {
    const claim = {...makeClaim(), lease_token: 'fixture-lease', evidence_source: 'conversation'};
    const row = {id: 'call-span', project_id: claim.project_id, trace_id: claim.trace_id, parent_span_id: '',
      name: 'Call Log', conversation: {turns: []}};
    const assessment = {outcome: 'unknown', findings: [], requirement_checks: []};
    const result = await investigateTrace(claim, {scratchRoot: scratch,
      control: async () => ({rows: [row]}),
      clickhouse: {baseUrl: 'http://clickhouse:8123', database: 'default', username: 'readonly', password: 'fixture',
        fetchImpl: async () => assert.fail('the stored span must not be read')},
      gatewayConfig: {baseUrl: 'http://fixture/v1', model: 'fixture', apiKey: 'fixture',
        fetchImpl: async (_url, init) => {
          const system = JSON.parse(init.body).messages.find(message => message.role === 'system').content;
          const content = JSON.stringify(system.includes('Independently check') ? assessment
            : {action: 'finish', question: '', child_instructions: '', assessment});
          return new Response(JSON.stringify({choices: [{message: {role: 'assistant', content},
            finish_reason: 'stop'}], usage: {prompt_tokens: 10, completion_tokens: 10}}),
          {headers: {'content-type': 'application/json', 'x-agentcc-cost': '0.000100'}});
        }}});
    assert.equal(result.execution_status, 'completed');
    assert.equal(result.coverage.observed_span_count, 1);
  } finally { await rm(scratch, {recursive: true, force: true}); }
});

test('an attempt the backend no longer holds as active ends before any stored-span read or model call', async () => {
  const scratch = await mkdtemp(join(tmpdir(), 'omega-conversation-evidence-test-'));
  try {
    const claim = {...makeClaim(), lease_token: 'fixture-lease', evidence_source: 'conversation'};
    let reads = 0, modelCalls = 0;
    const result = await investigateTrace(claim, {scratchRoot: scratch,
      control: async () => { throw Object.assign(new Error('Control request failed'), {status: 409}); },
      clickhouse: {baseUrl: 'http://clickhouse:8123', database: 'default', username: 'readonly', password: 'fixture',
        fetchImpl: async () => {
          reads++;
          return new Response(JSON.stringify({id: 'stored-span', project_id: claim.project_id,
            trace_id: claim.trace_id, parent_span_id: ''}) + '\n');
        }},
      gatewayConfig: {baseUrl: 'http://fixture/v1', model: 'fixture', apiKey: 'fixture',
        fetchImpl: async () => { modelCalls++; throw new TypeError('fetch failed'); }}});
    assert.deepEqual([reads, modelCalls, result.usage.model_calls], [0, 0, 0]);
    assert.equal(result.execution_status, 'failed');
    assert.equal(result.error_message, 'attempt_inactive');
  } finally { await rm(scratch, {recursive: true, force: true}); }
});

for (const [name, broken, fixed = {}] of [
  ['a recovery outside the allowed values', {recovery: 'Halted and delivered the full message before the caller hung up'}],
  ['a kind longer than its declared bound', {kind: 'k'.repeat(65)}],
  // The backend counts characters. The repair is 64 of them, which is 65 UTF-16 units.
  ['a kind one emoji over its declared bound', {kind: 'k'.repeat(64) + '👋'}, {kind: 'k'.repeat(63) + '👋'}]]) {
  for (const repaired of [true, false]) {
    test(`${name} is ${repaired ? 'repaired once' : 'a failed investigation when the repair breaks it too'}`, async () => {
      const scratch = await mkdtemp(join(tmpdir(), 'omega-output-bounds-test-'));
      try {
        const claim = makeClaim();
        const row = {id: '1234567890abcdef', project_id: claim.project_id, trace_id: claim.trace_id,
          parent_span_id: '', name: 'refund', input: 'Refund exactly 10', output: 'Refunded 5'};
        const raw = JSON.stringify(row);
        const evidenceId = `${row.id}:0:${Buffer.byteLength(raw)}`;
        const unknownRole = {status: 'unknown', span_id: null, evidence_ids: []};
        const assessment = overrides => ({outcome: 'failure', requirement_checks: [{requirement_id: 'refund-amount',
          requirement: 'Refund 10', status: 'violated', evidence_ids: [evidenceId]}],
        findings: [{finding_id: 'f1', kind: 'incorrect refund amount', statement: 'Only 5 refunded instead of 10',
          requirement_id: 'refund-amount', recovery: 'unrecovered', evidence_ids: [evidenceId],
          attribution: {origin: {status: 'supported', span_id: row.id, evidence_ids: [evidenceId],
            explanation: 'The refund span records 5 refunded against the request for 10.'},
          decisive: unknownRole, symptom: unknownRole}, ...overrides}]});
        let verdicts = 0;
        const result = await investigateTrace(claim, {scratchRoot: scratch,
          fetchEvidence: async (c, path) => storeEvidence([Buffer.from(raw + '\n')], path, c),
          gatewayConfig: {baseUrl: 'http://fixture/v1', model: 'fixture', apiKey: 'fixture',
            fetchImpl: async (_url, init) => {
              const request = JSON.parse(init.body);
              const system = request.messages.find(message => message.role === 'system').content;
              assert.match(system, /Set recovery to "recovered" when a later recorded step corrected the mistake/);
              let message;
              if (!request.messages.some(item => item.role === 'tool')) message = {role: 'assistant', content: '',
                tool_calls: [{id: 'read', type: 'function', function: {name: 'read_span',
                  arguments: JSON.stringify({span_id: row.id, offset: 0, length: 4096})}}]};
              else if (!system.includes('Independently check')) message = {role: 'assistant', content: JSON.stringify(
                {action: 'finish', question: '', child_instructions: '', assessment: assessment({})})};
              else message = {role: 'assistant', content: JSON.stringify(
                assessment(verdicts++ === 0 || !repaired ? broken : fixed))};
              return new Response(JSON.stringify({choices: [{message}], usage: {prompt_tokens: 10, completion_tokens: 5}}),
                {headers: {'Content-Type': 'application/json', 'x-agentcc-cost': '0'}});
            }}});
        // The first verdict breaks the schema; exactly one repair follows.
        assert.equal(verdicts, 2);
        if (repaired) {
          assert.equal(result.execution_status, 'completed');
          assert.deepEqual(result.findings.map(item => [item.kind, item.recovery]),
            [[fixed.kind ?? 'incorrect refund amount', 'unrecovered']]);
        } else {
          // A report Django would reject never leaves the worker as a completed one.
          assert.equal(result.execution_status, 'failed');
          assert.deepEqual(result.findings, []);
        }
      } finally { await rm(scratch, {recursive: true, force: true}); }
    });
  }
}

test('controller and verifier ask for plain-language finding statements', async () => {
  const scratch = await mkdtemp(join(tmpdir(), 'omega-wording-test-'));
  try {
    const claim = makeClaim();
    const row = {id: 'span-wording', project_id: claim.project_id, trace_id: claim.trace_id,
      input: 'Check the trace'};
    const assessment = {outcome: 'unknown', findings: [], requirement_checks: []};
    const systems = [];
    const result = await investigateTrace(claim, {scratchRoot: scratch,
      fetchEvidence: async (c, path) => storeEvidence([Buffer.from(JSON.stringify(row) + '\n')], path, c),
      gatewayConfig: {baseUrl: 'http://fixture/v1', model: 'fixture', apiKey: 'fixture',
        fetchImpl: async (_url, init) => {
          const system = JSON.parse(init.body).messages.find(message => message.role === 'system').content;
          systems.push(system);
          const content = JSON.stringify(system.includes('Independently check') ? assessment
            : {action: 'finish', question: '', child_instructions: '', assessment});
          return new Response(JSON.stringify({choices: [{message: {role: 'assistant', content},
            finish_reason: 'stop'}], usage: {prompt_tokens: 10, completion_tokens: 10}}),
          {headers: {'content-type': 'application/json', 'x-agentcc-cost': '0.000100'}});
        }}});
    assert.equal(result.execution_status, 'completed');
    assert.equal(systems.length, 2);
    for (const system of systems) {
      assert.match(system, /A goal that this situation made impossible to meet or to break is not a requirement of this trace/);
      assert.doesNotMatch(system, /whose stage the call never reached/);
      assert.match(system, /Obligations toward whoever or whatever did respond still apply/);
      // Conduct no requirement covers is still reported, as a finding with no requirement.
      assert.match(system, /Meeting its requirements does not settle how the agent did/);
      assert.match(system, /Conduct that failed them or wasted the interaction is a finding even when no requirement covers it: report it with requirement_id null\./);
      assert.match(system, /finding statement in plain words for a product reader/);
      assert.match(system, /short sentences of at most 25 words, one idea each, in active voice/);
      assert.match(system, /Never stack more than three nouns in a row/);
    }
  } finally { await rm(scratch, {recursive: true, force: true}); }
});

test('upstream HTTP status is retained in a failed investigation receipt', async () => {
  const scratch = await mkdtemp(join(tmpdir(), 'omega-upstream-test-'));
  try {
    const claim = makeClaim();
    const row = {id: 'span-upstream', project_id: claim.project_id, trace_id: claim.trace_id,
      input: 'Check the trace'};
    const result = await investigateTrace(claim, {scratchRoot: scratch,
      fetchEvidence: async (c, path) => storeEvidence([Buffer.from(JSON.stringify(row) + '\n')], path, c),
      gatewayConfig: {baseUrl: 'http://fixture/v1', model: 'fixture', apiKey: 'fixture',
        fetchImpl: async () => new Response('private upstream body', {status: 503})}});
    assert.equal(result.execution_status, 'failed');
    assert.equal(result.error_message, 'gateway_upstream_error:http_503');
    assert.equal(result.gateway_accounting.length, 1);
    assert.equal(result.gateway_accounting[0].raw.http_status, 503);
    assert.ok(!JSON.stringify(result).includes('private upstream body'));
  } finally { await rm(scratch, {recursive: true, force: true}); }
});

test('a failed evidence read publishes its reason and ClickHouse code inside the signed report', async () => {
  const scratch = await mkdtemp(join(tmpdir(), 'omega-evidence-failure-test-'));
  try {
    const result = await investigateTrace(makeClaim(), {scratchRoot: scratch,
      fetchEvidence: async () => { throw new EvidenceReadError(241); },
      gatewayConfig: {baseUrl: 'http://fixture/v1', model: 'fixture', apiKey: 'fixture',
        fetchImpl: async () => assert.fail('no model call may follow a failed evidence read')}});
    assert.equal(result.execution_status, 'failed');
    assert.equal(result.error_message, 'clickhouse_read_failed:code_241');
    assert.equal(result.usage.model_calls, 0);
    assert.equal(result.coverage.observed_span_count, 0);
    const {result_digest: digest, ...signed} = result;
    assert.equal(digest, canonicalDigest(signed));
    assert.equal((await investigateTrace(makeClaim(), {scratchRoot: scratch,
      fetchEvidence: async () => { throw new EvidenceReadError(null); },
      gatewayConfig: {baseUrl: 'http://fixture/v1', model: 'fixture', apiKey: 'fixture'}})).error_message,
    'clickhouse_read_failed');
  } finally { await rm(scratch, {recursive: true, force: true}); }
});

test('verifier call-budget exhaustion without an assessment remains a technical failure', async () => {
  const scratch = await mkdtemp(join(tmpdir(), 'omega-verifier-budget-test-'));
  try {
    const claim = makeClaim();
    claim.limits.max_model_calls = 4;
    const row = {id: 'span-verifier-budget', project_id: claim.project_id, trace_id: claim.trace_id,
      input: 'Check the trace'};
    const assessment = {outcome: 'unknown', findings: [], requirement_checks: []};
    let calls = 0;
    const result = await investigateTrace(claim, {scratchRoot: scratch,
      fetchEvidence: async (c, path) => storeEvidence([Buffer.from(JSON.stringify(row) + '\n')], path, c),
      gatewayConfig: {baseUrl: 'http://fixture/v1', model: 'fixture', apiKey: 'fixture',
        fetchImpl: async () => {
          calls++;
          const message = calls === 1
            ? {role: 'assistant', content: JSON.stringify({action: 'finish', question: '', child_instructions: '', assessment})}
            : {role: 'assistant', content: '', tool_calls: [{id: `read-${calls}`, type: 'function',
              function: {name: 'read_span', arguments: JSON.stringify({span_id: row.id, offset: 0, length: 5})}}]};
          return new Response(JSON.stringify({choices: [{message, finish_reason: 'tool_calls'}],
            usage: {prompt_tokens: 10, completion_tokens: 10}}),
          {headers: {'content-type': 'application/json', 'x-agentcc-cost': '0.000100'}});
        }}});
    assert.equal(result.execution_status, 'failed');
    assert.equal(result.error_message, 'call_budget_exhausted');
    assert.equal(result.outcome, 'unknown');
    assert.equal(result.usage.model_calls, 4);
  } finally { await rm(scratch, {recursive: true, force: true}); }
});

test('Omega compacts a long investigation transcript through the accounted gateway', async () => {
  const scratch = await mkdtemp(join(tmpdir(), 'omega-compaction-test-'));
  const spans = [];
  configureObservability({captureContent:true,rootContext:null,withParent:(_,span)=>span,
    provider:{shutdown:async()=>{}},tracer:{startSpan(name,options,parent) {
      const span={name,parent,attributes:{...options.attributes},ended:false,
        setAttributes(attrs){Object.assign(this.attributes,attrs);},setStatus(status){this.status=status;},
        end(){this.ended=true;}};
      spans.push(span);return span;
    }}});
  try {
    const claim = makeClaim();
    claim.limits.max_model_calls = 24;
    const row = {id: 'span-context', project_id: claim.project_id, trace_id: claim.trace_id,
      input: 'Investigate the recorded refund', output: 'Outcome unresolved'};
    const raw = JSON.stringify(row);
    const assessment = {outcome: 'unknown', findings: [], requirement_checks: []};
    let controllerCalls = 0, summaryCalls = 0, sawCompactedSummary = false;
    const result = await investigateTrace(claim, {scratchRoot: scratch, contextWindowTokens: 3000,
      fetchEvidence: async (c, path) => storeEvidence([Buffer.from(raw + '\n')], path, c),
      gatewayConfig: {baseUrl: 'http://fixture/v1', model: 'fixture', apiKey: 'fixture',
        fetchImpl: async (_url, init) => {
          const request = JSON.parse(init.body);
          const system = request.messages.find(message => message.role === 'system').content;
          sawCompactedSummary ||= request.messages.some(item =>
            typeof item.content === 'string' && item.content.includes('The refund outcome remains unresolved'));
          let message;
          if (system.startsWith('Summarize earlier investigation dialogue')) {
            summaryCalls++;
            message = {role: 'assistant', content: 'The refund outcome remains unresolved; re-read the span.'};
          } else if (system.includes('Independently check')) {
            message = {role: 'assistant', content: JSON.stringify(assessment)};
          } else if (++controllerCalls <= 6) {
            message = {role: 'assistant', content: '', tool_calls: [{id: `read-${controllerCalls}`,
              type: 'function', function: {name: 'read_span', arguments: JSON.stringify({
                span_id: row.id, offset: 0, length: 8})}}]};
          } else {
            message = {role: 'assistant', content: JSON.stringify({action: 'finish', question: '',
              child_instructions: '', assessment})};
          }
          return new Response(JSON.stringify({choices: [{message, finish_reason: 'stop'}],
            usage: {prompt_tokens: 100, completion_tokens: 10}}),
          {headers: {'content-type': 'application/json', 'x-agentcc-cost': '0.000100'}});
        }}});
    assert.equal(result.execution_status, 'completed');
    assert.ok(summaryCalls > 0);
    assert.equal(sawCompactedSummary, true);
    assert.equal(result.usage.model_calls, controllerCalls + summaryCalls + 1);
    assert.equal(Math.round(result.usage.cost_usd * 1e6), result.usage.model_calls * 100);
    const root = spans.find(span=>span.name==='error_feed.investigation');
    assert.equal(root.attributes['error_feed.attempt_id'],claim.attempt_id);
    assert.ok(spans.some(span=>span.name==='error_feed.agent.verifier' && span.parent===root));
    assert.ok(spans.some(span=>span.name==='error_feed.tool.read_span'
      && span.parent.name==='error_feed.agent.controller'));
    const modelSpans=spans.filter(span=>span.name==='error_feed.model');
    assert.equal(modelSpans.length,result.usage.model_calls);
    assert.ok(modelSpans.every(span=>span.parent.name.startsWith('error_feed.agent.')));
    assert.ok(spans.every(span=>span.ended));
    assert.ok(spans.every(span=>span.attributes['user.id']===claim.organization_id));
    assert.equal(JSON.parse(root.attributes['input.value']).trace_id, claim.trace_id);
    assert.equal(JSON.parse(root.attributes['output.value']).execution_status, 'completed');
    assert.ok(spans.every(span=>span.attributes['input.value'] && span.attributes['output.value']));
    assert.ok(modelSpans.every(span=>JSON.parse(span.attributes['input.value']).messages.length > 0));
    assert.ok(modelSpans.every(span=>span.attributes['gen_ai.cost.total'] === 0.0001));
  } finally { await stopObservability(); await rm(scratch, {recursive: true, force: true}); }
});

for (const scenario of ['controller_truncated', 'verifier_truncated', 'provider_overrun']) {
  test(`legacy token totals do not cap requests; truncated assessments remain safe: ${scenario}`, async () => {
    const scratch = await mkdtemp(join(tmpdir(), 'omega-budget-test-'));
    try {
      const claim = makeClaim();
      claim.limits.max_input_tokens_total = 1;
      claim.limits.max_output_tokens_total = 1;
      const row = {id: 'span-budget', project_id: claim.project_id, trace_id: claim.trace_id,
        input: 'Refund 10', output: 'Refunded 10'};
      const raw = JSON.stringify(row);
      const evidenceId = `${row.id}:0:${Buffer.byteLength(raw)}`;
      const assessment = {outcome: 'success', findings: [], requirement_checks: [
        {requirement_id: 'refund', requirement: 'Refund 10', status: 'satisfied', evidence_ids: [evidenceId]}]};
      const phases = [];
      const result = await investigateTrace(claim, {scratchRoot: scratch,
        fetchEvidence: async (c, path) => storeEvidence([Buffer.from(raw + '\n')], path, c),
        gatewayConfig: {baseUrl: 'http://fixture/v1', model: 'fixture', apiKey: 'fixture',
          fetchImpl: async (_url, init) => {
            const request = JSON.parse(init.body);
            const verifier = request.messages.find(m => m.role === 'system').content.includes('Independently check');
            phases.push(verifier ? 'verifier' : 'controller');
            assert.equal(Object.hasOwn(request, 'max_tokens'), false);
            let message, used, finishReason = 'stop';
            if (phases.length === 1) {
              used = 100;
              message = {role: 'assistant', content: '', tool_calls: [{id: 'read-budget', type: 'function',
                function: {name: 'read_span', arguments: JSON.stringify({span_id: row.id, offset: 0, length: 4096})}}]};
            } else if (!verifier) {
              used = 400;
              finishReason = 'length';
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
      assert.deepEqual(phases, ['controller', 'controller', 'verifier']);
      assert.equal(result.usage.model_calls, 3);
      assert.equal(result.usage.cost_usd, 0.0003);
      const failed = scenario === 'verifier_truncated';
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
        requirement_id: 'refund-amount', recovery: 'unrecovered', evidence_ids: [evidenceId],
        attribution: {origin: {status: 'supported', span_id: row.id, evidence_ids: [evidenceId],
          explanation: 'The refund span records 5 refunded against the request for 10, making it the first observed mismatch.'},
        decisive: unknownRole, symptom: unknownRole}}]};
    const phases = [];
    const result = await investigateTrace(claim, {scratchRoot: scratch,
      fetchEvidence: async (c, path) => storeEvidence([Buffer.from(raw + '\n')], path, c),
      gatewayConfig: {baseUrl: 'http://fixture/v1', model: 'fixture-model', apiKey: 'fixture-secret',
        fetchImpl: async (_url, init) => {
          const request = JSON.parse(init.body);
          const system = request.messages.find(message => message.role === 'system').content;
          const phase = system.includes('Independently check') ? 'verifier' : system.includes('Focused assignment') ? 'child' : 'controller';
          assert.match(system, /one concise sentence \(at most 600 characters\)/);
          if (phase === 'verifier') assert.match(system, /matches its cited evidence/);
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
    assert.match(result.findings[0].attribution.origin.explanation, /5 refunded against the request for 10/);
    assert.equal(result.findings[0].attribution.decisive.explanation, undefined);
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

test('role explanations are optional for legacy reports but only valid on supported roles', () => {
  const role = {status: 'supported', span_id: 'span-1', evidence_ids: ['e1']};
  const finding = {finding_id: 'f1', requirement_id: null, evidence_ids: ['e1'], attribution: {origin: role}};
  const report = {outcome: 'unknown', findings: [finding], requirement_checks: []};
  const receipts = [{evidence_id: 'e1', span_id: 'span-1'}];
  assert.doesNotThrow(() => validateAssessment(report, receipts));
  assert.doesNotThrow(() => validateAssessment({...report, findings: [{...finding, attribution: {
    origin: {...role, explanation: 'This span records the first observed mismatch.'}}}]}, receipts));
  for (const explanation of ['', '   ', 'x'.repeat(601), null, 7]) {
    assert.throws(() => validateAssessment({...report, findings: [{...finding, attribution: {
      origin: {...role, explanation}}}]}, receipts), /Invalid role explanation/);
  }
  for (const status of ['unknown', 'unsupported']) {
    assert.throws(() => validateAssessment({...report, findings: [{...finding, attribution: {
      origin: {status, span_id: null, evidence_ids: [], explanation: 'A guessed cause.'}}}]}, receipts),
    /Unsupported role explanation/);
  }
});

test('Node report digest matches the Django wire fixture', () => {
  assert.equal(canonicalDigest({read_cutoff: '2026-09-12T10:20:30.123456Z',
    gateway_accounting: [{cost: null, model_used: 'openai/test', raw: {charged: false}},
      {cost: 0, model_used: 'openai/test', raw: {units: 1}}, {cost: 1, model_used: 'openai/test', raw: null}],
    nested: {small: 0.001, large: 1000000000000000}}),
  'sha256:c0c2d86a98393d4a34d1ec1f7aabff4b5c134d06c7fb568185f607bbb91e09c8');
});

test('a failed simulation report carries no failure code', async () => {
  const scratch = await mkdtemp(join(tmpdir(), 'omega-simulation-investigation-test-'));
  try {
    const claim = {...makeClaim(), workload_type: 'simulation_test_execution',
      contract_version: 'omega-simulation/v1', test_execution_id: randomUUID(), lease_token: 'fixture-lease'};
    const result = await investigateSimulation(claim, {scratchRoot: scratch,
      control: async () => { throw new Error('control plane unavailable'); },
      gatewayConfig: {baseUrl: 'http://fixture/v1', model: 'fixture', apiKey: 'fixture',
        fetchImpl: async () => assert.fail('no model call may follow a failed evidence read')}});
    assert.equal(result.execution_status, 'failed');
    assert.ok(!('error_message' in result));
  } finally { await rm(scratch, {recursive: true, force: true}); }
});

test('simulation investigation reads call evidence and returns a scoped report', async () => {
  const scratch = await mkdtemp(join(tmpdir(), 'omega-simulation-investigation-test-'));
  try {
    const goalName = 'g'.repeat(256);
    const claim = {...makeClaim(), workload_type: 'simulation_test_execution',
      contract_version: 'omega-simulation/v1', test_execution_id: randomUUID(), lease_token: 'fixture-lease'};
    const row = {call_execution_id: randomUUID(), status: 'completed',
      simulation_call_type: 'conversation', scenario: 'Ask for help',
      call_summary: 'Agent answered', error_message: null, ended_reason: null,
      transcript: [{id: randomUUID(), speaker: 'assistant', content: 'How can I help?', start_time: 1, end_time: 2}],
      goals: {use_case: 'Get help', sub_goals: [goalName], expected_outcome: 'The agent greets the caller'}};
    const evidenceId = `${row.call_execution_id}:0:${Buffer.byteLength(JSON.stringify(row))}`;
    const assessment = {outcome: 'success', findings: [], requirement_checks: [
      {requirement_id: goalName, requirement: 'Agent greets the caller', status: 'satisfied',
        evidence_ids: [evidenceId]}]};
    const controlCalls = [];
    const systems = [];
    const result = await investigateSimulation(claim, {scratchRoot: scratch,
      control: async (path, body) => {
        controlCalls.push({path, body});
        return {calls: [row], next_cursor: 1, total_calls: 1};
      },
      gatewayConfig: {baseUrl: 'http://fixture/v1', model: 'fixture-model', apiKey: 'fixture-secret',
        fetchImpl: async (_url, init) => {
          const request = JSON.parse(init.body);
          const system = request.messages.find(message => message.role === 'system').content;
          systems.push(system);
          const verifier = system.includes('Independently check');
          const toolMessage = request.messages.find(message => message.role === 'tool');
          if (!verifier && !toolMessage) {
            // The trace-only changes leave a simulation stage input and its tools as they were.
            assert.equal(JSON.parse(request.messages.find(message => message.role === 'user').content)
              .coverage.read_complete, false);
            assert.deepEqual(request.tools.map(item => item.function.name),
              ['list_simulation_calls', 'read_simulation_call']);
          }
          const message = !toolMessage
            ? {role: 'assistant', content: '', tool_calls: [{id: 'read-call', type: 'function',
              function: {name: 'read_simulation_call', arguments: JSON.stringify({
                call_execution_id: row.call_execution_id, offset: 0, length: 4096})}}]}
            : {role: 'assistant', content: JSON.stringify(verifier ? assessment : {
              action: 'finish', question: '', child_instructions: '', assessment})};
          return new Response(JSON.stringify({choices: [{message}], usage: {prompt_tokens: 10, completion_tokens: 5}}),
            {headers: {'Content-Type': 'application/json', 'x-agentcc-cost': '0'}});
        }}});
    assert.equal(result.execution_status, 'completed');
    assert.equal(result.outcome, 'success');
    assert.equal(result.test_execution_id, claim.test_execution_id);
    assert.equal(result.coverage.read_complete, true);
    assert.deepEqual(result.evidence_receipts.map(item => item.call_execution_id), [row.call_execution_id]);
    assert.deepEqual(controlCalls, [{path: `/attempts/${claim.attempt_id}/simulation-evidence/`,
      body: {lease_token: claim.lease_token, cursor: 0}}]);
    assert.ok(systems.length > 0);
    for (const system of systems) {
      assert.doesNotMatch(system, /impossible to meet or to break/);
      assert.doesNotMatch(system, /Conduct that failed them or wasted the interaction/);
      // A sub-goal the call never reached is left out, so it cannot leave the call without a verdict.
      assert.match(system, /check each one the call put to the test/);
      assert.match(system, /A sub-goal whose stage the call never reached, or whose trigger never occurred, could be neither met nor broken: it is not a requirement of this call, so do not list it\./);
      assert.match(system, /Obligations toward the caller in the situation that did occur still apply/);
      assert.match(system, /finding statement in plain words for a product reader/);
      assert.match(system, /short sentences of at most 25 words, one idea each, in active voice/);
      assert.match(system, /Never stack more than three nouns in a row/);
    }
  } finally {
    await rm(scratch, {recursive: true, force: true});
  }
});

test('a simulation failure claimed without a violated sub-goal publishes what its checks support', async () => {
  const scratch = await mkdtemp(join(tmpdir(), 'omega-simulation-investigation-test-'));
  const events = [];
  const write = process.stderr.write;
  process.stderr.write = chunk => {
    try { events.push(JSON.parse(chunk)); } catch { /* not a worker event */ }
    return true;
  };
  try {
    const claim = {...makeClaim(), workload_type: 'simulation_test_execution',
      contract_version: 'omega-simulation/v1', test_execution_id: randomUUID(), lease_token: 'fixture-lease'};
    const row = {call_execution_id: randomUUID(), status: 'completed',
      simulation_call_type: 'conversation', scenario: 'Ask for help',
      call_summary: 'Agent answered', error_message: null, ended_reason: null,
      transcript: [{id: randomUUID(), speaker: 'assistant', content: 'How can I help?', start_time: 1, end_time: 2}],
      goals: {use_case: 'Get help', sub_goals: ['greeting'], expected_outcome: 'The agent greets the caller'}};
    const evidenceId = `${row.call_execution_id}:0:${Buffer.byteLength(JSON.stringify(row))}`;
    const unknownRole = {status: 'unknown', call_execution_id: null, evidence_ids: []};
    // The verifier saw the call go wrong for a reason no authored sub-goal covers.
    const assessment = {outcome: 'failure',
      findings: [{finding_id: 'f1', kind: 'caller_left_waiting', statement: 'The agent left the caller waiting.',
        requirement_id: null, evidence_ids: [evidenceId], recovery: 'unrecovered',
        attribution: {origin: unknownRole, decisive: unknownRole, symptom: unknownRole}}],
      requirement_checks: [{requirement_id: 'greeting', requirement: 'Agent greets the caller', status: 'satisfied',
        evidence_ids: [evidenceId]}]};
    const result = await investigateSimulation(claim, {scratchRoot: scratch,
      control: async () => ({calls: [row], next_cursor: 1, total_calls: 1}),
      gatewayConfig: {baseUrl: 'http://fixture/v1', model: 'fixture', apiKey: 'fixture',
        fetchImpl: async (_url, init) => {
          const request = JSON.parse(init.body);
          const verifier = request.messages.find(m => m.role === 'system').content.includes('Independently check');
          const message = !request.messages.some(m => m.role === 'tool')
            ? {role: 'assistant', content: '', tool_calls: [{id: 'read-call', type: 'function',
              function: {name: 'read_simulation_call', arguments: JSON.stringify({
                call_execution_id: row.call_execution_id, offset: 0, length: 4096})}}]}
            : {role: 'assistant', content: JSON.stringify(verifier ? assessment
              : {action: 'finish', question: '', child_instructions: '', assessment})};
          return new Response(JSON.stringify({choices: [{message}], usage: {prompt_tokens: 10, completion_tokens: 5}}),
            {headers: {'content-type': 'application/json', 'x-agentcc-cost': '0'}});
        }}});
    // This answer used to end the attempt with no report, and the call showed as not analysed.
    assert.equal(result.execution_status, 'completed');
    assert.equal(result.outcome, 'success');
    assert.deepEqual(result.findings.map(item => [item.finding_id, item.requirement_id, item.recovery]),
      [['f1', null, 'unrecovered']]);
    assert.deepEqual(events.filter(event => event.event === 'omega_outcome_derived'), [{event: 'omega_outcome_derived',
      attempt_id: claim.attempt_id, phase: 'verifier', model_outcome: 'failure', derived_outcome: 'success'}]);
  } finally {
    process.stderr.write = write;
    await rm(scratch, {recursive: true, force: true});
  }
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
      requirement_id: 'refund', evidence_ids: [evidenceId], recovery: 'unrecovered',
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

test('the requirement checks decide the outcome', () => {
  const checks = (...statuses) => ({requirement_checks: statuses.map(status => ({status}))});
  const complete = {read_complete: true};
  assert.equal(deriveOutcome(checks('satisfied', 'violated'), complete), 'failure');
  assert.equal(deriveOutcome(checks('violated'), {read_complete: false}), 'failure');
  assert.equal(deriveOutcome(checks('satisfied', 'satisfied'), complete), 'success');
  assert.equal(deriveOutcome(checks('satisfied', 'unknown'), complete), 'unknown');
  assert.equal(deriveOutcome(checks(), complete), 'unknown');
  assert.equal(deriveOutcome(checks('satisfied'), {read_complete: false}), 'unknown');
});

// A clean trace: the controller reads the first span once, then both stages answer.
async function investigateCleanTrace(verifierAnswer, {extraSpans = 0} = {}) {
  const scratch = await mkdtemp(join(tmpdir(), 'omega-verdict-test-'));
  const requests = [], events = [];
  const write = process.stderr.write;
  process.stderr.write = chunk => {
    try { events.push(JSON.parse(chunk)); } catch { /* not a worker event */ }
    return true;
  };
  try {
    const claim = makeClaim();
    const row = {id: 'first-span', project_id: claim.project_id, trace_id: claim.trace_id,
      parent_span_id: '', input: 'Refund 10', output: 'Refunded 10'};
    const raw = JSON.stringify(row);
    const rows = [raw, ...Array.from({length: extraSpans}, (_, index) => JSON.stringify(
      {...row, id: 'later-span-' + index, parent_span_id: row.id}))].join('\n') + '\n';
    const answer = verifierAnswer(`${row.id}:0:${Buffer.byteLength(raw)}`);
    const result = await investigateTrace(claim, {scratchRoot: scratch,
      fetchEvidence: async (c, path) => storeEvidence([Buffer.from(rows)], path, c),
      gatewayConfig: {baseUrl: 'http://fixture/v1', model: 'fixture', apiKey: 'fixture',
        fetchImpl: async (_url, init) => {
          const request = JSON.parse(init.body);
          const verifier = request.messages.find(m => m.role === 'system').content.includes('Independently check');
          requests.push({verifier, tools: (request.tools ?? []).map(item => item.function.name),
            input: JSON.parse(request.messages.find(m => m.role === 'user').content)});
          const message = !verifier && !request.messages.some(m => m.role === 'tool')
            ? {role: 'assistant', content: '', tool_calls: [{id: 'read-first', type: 'function',
              function: {name: 'read_span', arguments: JSON.stringify({span_id: row.id, offset: 0, length: 4096})}}]}
            : {role: 'assistant', content: JSON.stringify(verifier ? answer
              : {action: 'finish', question: '', child_instructions: '', assessment: answer})};
          return new Response(JSON.stringify({choices: [{message}], usage: {prompt_tokens: 10, completion_tokens: 5}}),
            {headers: {'content-type': 'application/json', 'x-agentcc-cost': '0'}});
        }}});
    return {result, requests, events, claim};
  } finally {
    process.stderr.write = write;
    await rm(scratch, {recursive: true, force: true});
  }
}

const refundCheck = (status, evidenceIds) => ({requirement_id: 'refund', requirement: 'Refund 10', status,
  evidence_ids: evidenceIds});

test('a fully read trace with every check satisfied is a success whatever outcome the verifier chose', async () => {
  const {result, events, claim} = await investigateCleanTrace(evidenceId => ({outcome: 'unknown', findings: [],
    requirement_checks: [refundCheck('satisfied', [evidenceId])]}));
  assert.equal(result.execution_status, 'completed');
  assert.equal(result.outcome, 'success');
  assert.deepEqual(events.filter(event => event.event === 'omega_outcome_derived'), [{event: 'omega_outcome_derived',
    attempt_id: claim.attempt_id, phase: 'verifier', model_outcome: 'unknown', derived_outcome: 'success'}]);
});

test('a failure claimed without a violated requirement publishes what its checks support', async () => {
  const {result, events} = await investigateCleanTrace(evidenceId => ({outcome: 'failure', findings: [],
    requirement_checks: [refundCheck('satisfied', [evidenceId]),
      {requirement_id: 'receipt', requirement: 'Send a receipt', status: 'unknown', evidence_ids: []}]}));
  // This answer used to end the attempt with missing_violated_requirement and no report.
  assert.equal(result.execution_status, 'completed');
  assert.ok(!('error_message' in result));
  assert.equal(result.outcome, 'unknown');
  assert.equal(events.filter(event => event.event === 'omega_outcome_derived')[0].model_outcome, 'failure');
});

test('a stage is not told the evidence is unread before it has read it', async () => {
  const {requests} = await investigateCleanTrace(() => ({outcome: 'unknown', findings: [], requirement_checks: []}));
  const controller = requests.find(request => !request.verifier).input;
  assert.ok(!('read_complete' in controller.coverage));
  assert.equal(controller.coverage.observed_span_count, 1);
  assert.equal(requests.find(request => request.verifier).input.coverage.read_complete, true);
});

test('a trace inventory of one page replaces the list tool', async () => {
  const answer = () => ({outcome: 'unknown', findings: [], requirement_checks: []});
  for (const request of (await investigateCleanTrace(answer)).requests) {
    assert.deepEqual(request.tools, ['read_span']);
    assert.deepEqual(request.input.available_capabilities, ['read_span']);
  }
  const paged = (await investigateCleanTrace(answer, {extraSpans: 20})).requests;
  assert.equal(paged[0].input.inventory.more, true);
  for (const request of paged) {
    assert.deepEqual(request.tools, ['list_spans', 'read_span']);
    assert.deepEqual(request.input.available_capabilities, ['list_spans', 'read_span']);
  }
});

test('an audio-grounded finding survives the verifier and publishes its audio receipt', async () => {
  const scratch = await mkdtemp(join(tmpdir(), 'omega-audio-evidence-test-'));
  try {
    const claim = makeClaim();
    const row = {id: 'audio-span', project_id: claim.project_id, trace_id: claim.trace_id, parent_span_id: '',
      input: 'Leave a voicemail when nobody answers', output: '(unintelligible audio)'};
    const raw = JSON.stringify(row);
    const spanEvidence = `${row.id}:0:${Buffer.byteLength(raw)}`;
    const audioEvidence = 'audio:audio-span:1';
    const role = (status, ids) => status === 'supported'
      ? {status, span_id: row.id, evidence_ids: ids, explanation: 'The recording shows a Spanish voicemail greeting.'}
      : {status, span_id: null, evidence_ids: []};
    const assessment = {outcome: 'failure', requirement_checks: [{requirement_id: 'voicemail',
      requirement: 'Leave a voicemail when nobody answers', status: 'violated', evidence_ids: [audioEvidence]}],
      findings: [{finding_id: 'spanish-voicemail', kind: 'unhandled_non_english_voicemail',
        statement: 'When the call reached a Spanish voicemail greeting, the agent repeated its English opener instead of leaving a message.',
        requirement_id: 'voicemail', evidence_ids: [audioEvidence, spanEvidence], recovery: 'unrecovered',
        attribution: {origin: role('unknown'), decisive: role('supported', [audioEvidence]), symptom: role('unknown')}}]};
    let controllerStep = 0, verifierInput, verifierSystem;
    const result = await investigateTrace(claim, {scratchRoot: scratch,
      resolveRecording: async () => ({url: 'https://media.example.test/call.wav', format: 'wav'}),
      fetchEvidence: async (c, path) => storeEvidence([Buffer.from(raw + '\n')], path, c),
      gatewayConfig: {baseUrl: 'https://gateway.invalid/v1', model: 'fixture', apiKey: 'fixture',
        fetchImpl: async (_url, init) => {
          const request = JSON.parse(init.body);
          let message;
          if (Array.isArray(request.messages[0]?.content)) {
            message = {role: 'assistant', content: JSON.stringify({answer: 'An automated voicemail greeting in Spanish',
              observations: [{statement: 'Tu llamada se reenvio al buzon de voz', start_seconds: 0, end_seconds: 11,
                speaker: 'voicemail', confidence: 0.1}],
              metrics: [{name: 'silence_duration', value: '17', unit: 'seconds', method: 'gap from the beep to hangup'}],
              uncertainty: null})};
          } else if (request.messages.find(m => m.role === 'system').content.includes('Independently check')) {
            verifierInput = JSON.parse(request.messages.find(m => m.role === 'user').content);
            verifierSystem = request.messages.find(m => m.role === 'system').content;
            message = {role: 'assistant', content: JSON.stringify(assessment)};
          } else if (controllerStep++ === 0) {
            message = {role: 'assistant', content: '', tool_calls: [{id: 'read-span', type: 'function',
              function: {name: 'read_span', arguments: JSON.stringify({span_id: row.id, offset: 0, length: 4096})}}]};
          } else if (controllerStep === 2) {
            message = {role: 'assistant', content: '', tool_calls: [{id: 'ask-audio', type: 'function',
              function: {name: 'inspect_audio', arguments: JSON.stringify({span_id: row.id,
                message: 'Who answered the call, and in which language?'})}}]};
          } else {
            message = {role: 'assistant', content: JSON.stringify({action: 'finish', question: '',
              child_instructions: '', assessment})};
          }
          return new Response(JSON.stringify({choices: [{message}], usage: {prompt_tokens: 20, completion_tokens: 20}}),
            {headers: {'content-type': 'application/json', 'x-agentcc-cost': '0.000100', 'x-request-id': 'gateway-request'}});
        }}});
    assert.equal(result.execution_status, 'completed');
    assert.equal(result.outcome, 'failure');
    assert.deepEqual(verifierInput.audio_observations.map(item => item.evidence_id), [audioEvidence]);
    assert.match(verifierInput.audio_observations[0].excerpt, /voicemail greeting in Spanish/);
    assert.ok(verifierInput.observed_evidence_ids.includes(audioEvidence));
    assert.equal(result.findings[0].evidence_ids[0], audioEvidence);
    const published = result.evidence_receipts.find(item => item.evidence_id === audioEvidence);
    assert.equal(published.span_id, row.id);
    assert.match(published.excerpt, /^Audio model observation of the recording/);
    for (const excerpt of [verifierInput.audio_observations[0].excerpt, published.excerpt]) {
      assert.match(excerpt, /buzon de voz \(confidence 0\.1\)/);
      assert.match(excerpt, /Metric silence_duration: 17 seconds \(method: gap from the beep to hangup\)/);
    }
    assert.match(verifierSystem, /primary evidence of what was said and heard/);
    assert.match(verifierSystem, /Timed transcript words are still evidence that speech happened/);
    assert.match(verifierSystem, /must cite evidence IDs returned by read_span or inspect_audio\./);
    assert.match(verifierSystem, /an audio receipt supports what the audio model heard even when no span text contains it/);
    assert.doesNotMatch(verifierSystem, /returned by read_span\. /);
    assert.match(verifierSystem, /A goal that this situation made impossible to meet or to break is not a requirement of this trace/);
  } finally {await rm(scratch, {recursive: true, force: true});}
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
                message: 'Did the assistant interrupt the caller?'})}}]};
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
