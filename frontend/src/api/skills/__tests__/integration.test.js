import { describe, expect, it } from 'vitest';
import { mapContextDraft, mapDraftOperation, requireAcknowledgedDraft, contextParts, mapContextSummary } from '../context-authoring';
import { checkpointCreateInput, retainedPlanKeepInput, retainedPlanAcceptanceInput, mapCheckpoint } from '../r3-intake';
import { mapSpendSummary, spendPolicyInput } from '../r3-spend';
import { contextBindingInput, mapContextBinding, mapVersionSafety } from '../context-consumers';
import { skillsKeys } from '../keys';
import wire from './r3_l07/wire-fixtures.json';

const digest = `sha256:${'a'.repeat(64)}`;
const recovery = `sha256:${'b'.repeat(64)}`;
const draft = { base_version: 4, expected_head_version: 6, revision: 12, files: [{ path: 'AGENTS.md', content: 'Exact\r\nbytes\n' }], note: 'Reviewed', context_spec: wire.ContextSpec, resolved_lock: wire.ResolvedLock, digest, recovery_digest: recovery, checks: { items: [], summary: { errors: 0, warnings: 0, infos: 0 } }, updated_at: '2026-11-08T00:00:00Z' };

describe('integrated human-authoring boundaries', () => {
  it('keeps origin/head, compiled digest and recovery digest separate without rewriting source bytes', () => {
    expect(mapContextDraft(draft)).toMatchObject({ revision: 12, baseVersion: 4, expectedHeadVersion: 6, digest, recoveryDigest: recovery, files: draft.files });
    expect(mapContextDraft({ ...draft, recovery_digest: undefined }).recoveryDigest).toBeUndefined();
  });
  it('hydrates only the exact acknowledged revision, not a later edit after lost response', () => {
    const receipt = mapDraftOperation({ context_id: 'context', draft_id: 'draft', revision: 12, digest });
    const original = mapContextDraft(draft);
    expect(requireAcknowledgedDraft(receipt, original)).toBe(original);
    expect(() => requireAcknowledgedDraft(receipt, { ...original, revision: 13 })).toThrow('live draft has changed');
    expect(() => requireAcknowledgedDraft(receipt, { ...original, digest: recovery })).toThrow('live draft has changed');
    expect(() => requireAcknowledgedDraft(receipt, null)).toThrow('live draft has changed');
    expect(receipt.draft).toBeNull();
  });
  it('rejects a leaf summary and partitions context data by kind and actual principal', () => {
    expect(() => mapContextSummary({ kind: 'skill' })).toThrow('leaf skill');
    const scope = { organizationId: 'org', workspaceId: 'workspace', userId: 'real-user' };
    const context = skillsKeys.v11(scope, ...contextParts('resource', 'draft'));
    expect(context).not.toEqual(skillsKeys.v11(scope, 'legacy', 'resource', 'draft'));
    expect(context).not.toEqual(skillsKeys.v11({ ...scope, userId: 'other-user' }, ...contextParts('resource', 'draft')));
  });
});

describe('retained decisions and checkpoints', () => {
  it('maps suspension to the actual serializer and rejects missing recovery identities', () => {
    expect(checkpointCreateInput({ revision: 12, expectedDigest: recovery, reason: 'switch' })).toEqual({ revision: 12, expected_digest: recovery, reason: 'suspension', suspend: true });
    expect(checkpointCreateInput({ revision: 12, expectedDigest: recovery, reason: 'deletion', suspend: false }).suspend).toBe(false);
    expect(() => checkpointCreateInput({ revision: 12, reason: 'deletion' })).toThrow('recovery digest');
    expect(() => checkpointCreateInput({ revision: 12, expectedDigest: recovery, reason: 'unknown' })).toThrow('reason');
  });
  it('retains expiry and original-identity metadata after checkpoint payload purge', () => {
    expect(mapCheckpoint({ id: 'checkpoint', resource_id: 'original', draft_revision: 12, digest: recovery, payload_expired: true, restored_revision: 14 })).toMatchObject({ resourceId: 'original', draftRevision: 12, digest: recovery, payloadExpired: true, restoredRevision: 14 });
  });
  it('does not confuse keep with replacement or omitted tags with an explicit clear', () => {
    const input = { planId: 'plan', expectedFingerprint: digest, itemKey: 'item', mode: 'snapshot', expectedHeadVersion: 4, expectedHeadDigest: digest, rightsConfirmed: true };
    expect(retainedPlanKeepInput(input).decision).toBe('keep');
    expect(retainedPlanAcceptanceInput(input).decision).toBe('replace');
    expect(retainedPlanAcceptanceInput({ ...input, decision: 'replace' })).toEqual(retainedPlanAcceptanceInput(input));
    expect(retainedPlanAcceptanceInput(input)).not.toHaveProperty('tags');
    expect(retainedPlanAcceptanceInput({ ...input, tags: [] }).tags).toEqual([]);
  });
  it.each(['keep', 'manual', undefined, null])('never changes an explicit %s decision into replacement', (decision) => {
    expect(() => retainedPlanAcceptanceInput({ decision })).toThrow('separate reviewed decision');
  });
});

it('maps decimal accounting without rounding, defaults or authorization inference', () => {
  const value = mapSpendSummary({ revision: 3, enabled: false, period_limit_usd: null, remaining_usd: null, known_estimate_usd: '0.00000001', unknown_reserved_usd: '12.00000000', prior_period_unknown_usd: '10.00000000', committed_usd: '2.00000001', active_senders: 1 });
  expect(value).toMatchObject({ periodLimitUsd: null, remainingUsd: null, knownEstimateUsd: '0.00000001', priorPeriodUnknownUsd: '10.00000000', activeSenders: 1 });
  expect(spendPolicyInput({ revision: 3, enabled: true, periodLimitUsd: '0.00000001' }).period_limit_usd).toBe('0.00000001');
  expect(() => spendPolicyInput({ periodLimitUsd: 1 })).toThrow('decimal string');
});
it('round-trips the binding contract without inventing native configuration or inferred observations', () => {
  const mapped = mapContextBinding(wire.BindingOut);
  expect(contextBindingInput(mapped)).toMatchObject({ selector: wire.BindingOut.selector, profile_version: wire.BindingOut.profile_version, repository: wire.BindingOut.repository });
  expect(mapped).not.toHaveProperty('loaded');
  expect(contextBindingInput(mapped)).not.toHaveProperty('configuration');
  expect(mapVersionSafety({ revision: 2, quarantined: true, reason: 'review', replacement_version_id: null, affected_context_count: 3 })).toEqual({ revision: 2, quarantined: true, reason: 'review', replacementVersionId: null, affectedContextCount: 3 });
});
