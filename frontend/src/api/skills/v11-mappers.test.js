import { describe, expect, it } from 'vitest';
import { assistInput, caseInput, mapAdoption, mapCall, mapCase, mapCaseResult, mapExecutionConfiguration, mapLimits, mapOutboundFinding, mapRunFile, mapCost, runConfigInput } from './v11-mappers';

describe('frozen evidence mapping', () => {
  it('uses historical model configuration and retains requested and returned model identifiers', () => {
    expect(mapExecutionConfiguration({ model: { model_id: 'historical-task', temperature: 0, max_output_tokens: 12 }, judge: null, harness_version: 'old-harness', adapter_version: 'old-adapter', evaluator_version: 'old-evaluator' })).toEqual({ model: { modelId: 'historical-task', temperature: 0, maxOutputTokens: 12 }, judge: null, harnessVersion: 'old-harness', adapterVersion: 'old-adapter', evaluatorVersion: 'old-evaluator' });
    const message = { tool_calls: [{ function: { arguments: '{"user_defined_key":true}' } }] };
    const call = mapCall({ requested_model: 'requested', returned_model: 'returned-version', assistant_message: message, usage: { known_total_tokens: null, unknown_calls: 1 }, cost: { known_estimate_usd: null, reserved_estimate_usd: '0.25000000' } });
    expect(call).toMatchObject({ requestedModel: 'requested', returnedModel: 'returned-version', assistantMessage: message, usage: { knownTotalTokens: null }, cost: { knownEstimateUsd: null, reservedEstimateUsd: '0.25000000' } });
    expect(call.assistantMessage).toBe(message);
  });
  it('does not turn unknown spend into zero or convert decimal USD strings into floats', () => {
    expect(mapCost({ known_estimate_usd: null, reserved_estimate_usd: '0.00000001', unknown_calls: 1, complete: false, pricing_source: 'frozen' })).toEqual({ knownEstimateUsd: null, reservedEstimateUsd: '0.00000001', unknownCalls: 1, complete: false, pricingSource: 'frozen' });
  });
  it('preserves the server mixed reducer instead of choosing a preferred baseline', () => {
    expect(mapCaseResult({ case_id: 'case', title: 'Boundary', expected_trigger: null, change: 'mixed', variants: [], answer_comparisons: [] }).change).toBe('mixed');
  });
  it('maps exact profile ceilings and effective deployment overrides without hardcoding units', () => {
    const profile = { max_cases: 2, max_variants: 2, repetitions: 1, max_attempts: 4, deadline_seconds: 222, max_task_turns_per_attempt: 6, max_tool_calls_per_attempt: 12, task_phase_seconds: 90, judge_phase_seconds: 30, provider_call_seconds: 45, max_task_output_tokens: 1234, max_judge_output_tokens: 2048, max_delivered_bytes_per_attempt: 196608, max_total_tokens: 10000 };
    const value = mapLimits({ profiles: { quick: profile, repeated: { ...profile, repetitions: 3 } }, max_competitors: 4, max_active_attempts_per_run: 1, max_active_provider_calls_per_organization: 3, max_active_model_jobs_per_organization: 1, max_queue_wait_seconds: 50, max_estimated_cost_usd: '0.12345678' });
    expect(value.profiles.quick).toMatchObject({ maxCases: 2, deadlineSeconds: 222, maxDeliveredBytesPerAttempt: 196608, maxTaskOutputTokens: 1234 });
    expect(value.profiles.repeated.repetitions).toBe(3);
    expect(value.maxEstimatedCostUsd).toBe('0.12345678');
    expect(value.maxActiveProviderCallsPerOrganization).toBe(3);
  });
  it('makes stable finding IDs and sanitized case/fixture locations available for review', () => {
    expect(mapOutboundFinding({ finding_id: 'stable', code: 'secret_suspected', explanation: 'Redacted', location: { kind: 'fixture', case_id: 'case', fixture_id: 'fixture', line: 3 } })).toMatchObject({ findingId: 'stable', explanation: 'Redacted', location: { kind: 'fixture', caseId: 'case', fixtureId: 'fixture', line: 3 } });
  });
  it('preserves assistance input pointers instead of inventing a bundle path', () => {
    expect(mapOutboundFinding({ finding_id: 'assist-finding', code: 'secret_suspected', explanation: 'Redacted', location: { kind: 'assist_input', pointer: '/context_text', line: 2 } })).toMatchObject({ findingId: 'assist-finding', location: { kind: 'assist_input', pointer: '/context_text', line: 2 } });
  });
  it('preserves character offsets and immutable file membership', () => {
    expect(mapRunFile({ skill_id: 'target', version_id: null, digest: 'sha256:test', path: 'SKILL.md', text: '😀', offset: 0, next_offset: 1, eof: false, total_chars: 2, size_bytes: 5 })).toMatchObject({ skillId: 'target', versionId: null, digest: 'sha256:test', nextOffset: 1, totalChars: 2, text: '😀' });
  });
});

describe('explicit request mappings', () => {
  it('round-trips assertions without renaming user-defined JSON or accepting review attribution', () => {
    const json = { expected_trigger: false, camelCase: true };
    const wire = { id: 'case', title: 'Output', source_group_id: 'group', prompt: 'Request', fixtures: [], expected_trigger: null, checks: [{ id: 'equals', kind: 'json_equals', critical: true, config: { pointer: '/expected_trigger', value: json } }, { id: 'literal', kind: 'contains', critical: false, config: { text: 'Case', case_sensitive: false } }], reviewed: true, reviewed_by: { id: 'owner' }, reviewed_at: 'date', exposed_to_assist: false, origin: 'manual' };
    expect(caseInput(mapCase(wire))).toEqual({ id: wire.id, title: wire.title, source_group_id: wire.source_group_id, prompt: wire.prompt, fixtures: [], expected_trigger: null, checks: wire.checks, reviewed: true });
    expect(caseInput(mapCase(wire)).checks[0].config.value).toBe(json);
  });
  it('keeps the canonical draft revision and null judge in a start configuration', () => {
    const mapped = runConfigInput({ candidate: { kind: 'draft', revision: 7, digest: 'digest' }, cases: { kind: 'saved', suiteRevision: 2, caseIds: ['case'] }, baselines: ['production', 'without_target'], competitors: [{ skillId: 'other', version: 8 }], model: { modelId: 'model', temperature: 0, maxOutputTokens: 100 }, judge: null, profile: 'repeated', repetitions: 3, limits: { maxTotalTokens: 1000, maxEstimatedCostUsd: '1.00' }, purpose: 'regression', parentRunId: null });
    expect(mapped).toMatchObject({ candidate: { revision: 7 }, cases: { suite_revision: 2 }, competitors: [{ skill_id: 'other', version: 8 }], judge: null, parent_run_id: null });
    expect(mapped.candidate).not.toHaveProperty('expected_draft_revision');
  });
  it('maps summary input with no invented draft or revision field', () => {
    expect(assistInput({ kind: 'summary', fromVersion: null, to: { kind: 'version', version: 3, digest: 'digest' }, model: { modelId: 'model', temperature: 0, maxOutputTokens: 100 }, maxEstimatedCostUsd: '1.00' })).toEqual({ kind: 'summary', from_version: null, to: { kind: 'version', version: 3, digest: 'digest' }, model: { model_id: 'model', temperature: 0, max_output_tokens: 100 }, max_estimated_cost_usd: '1.00' });
  });
});

it('distinguishes absent adoption target and observed counts from a fleet denominator', () => {
  const value = mapAdoption({ target: null, window: { start: 'start', end: 'end', as_of: 'as-of' }, coverage: 'observed_fetches_only', historical_timestamp_quality: 'best_effort', summary: { current_only: 0, other_only: 0, mixed: 0, not_seen: 0, ambiguous: 0 }, results: [], count: 0, page: 1, page_size: 25, total_pages: 0 });
  expect(value).toMatchObject({ target: null, coverage: 'observed_fetches_only', count: 0, pageSize: 25, summary: { notSeen: 0 }, window: { asOf: 'as-of' } });
  expect(value).not.toHaveProperty('fleetSize');
});
