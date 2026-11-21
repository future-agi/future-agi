import { describe, expect, it } from 'vitest';
import { mapMigrationPlan, mapMigrationReview, migrationDecisionInput } from './migration';

const digest = `sha256:${'a'.repeat(64)}`;
describe('retained migration wire contract', () => {
  it('retains incomplete members and immutable identities instead of accepting partial packages', () => {
    const plan = mapMigrationPlan({ id: 'plan', fingerprint: digest, expires_at: '2099-01-01', inventory: {
      rejected: [{ path: 'bad/a.bin', reason: 'file_not_text' }], skipped: ['.DS_Store'], items: [{
        key: 'bad', root: 'bad', name: 'bad', kind: 'skill', blockers: [{ path: 'a.bin', reason: 'file_not_text' }],
        files: [{ path: 'SKILL.md', size_bytes: 42 }], destination_id: 'destination',
        expected_head_version: 2, expected_head_digest: digest,
        source: { origin: 'source.example.test', project: 'p', resource: 'r', revision: '4' },
        migration: { hub_diverged: true, files: [] },
      }],
    } });
    expect(plan.items[0]).toMatchObject({ blocked: true, sourceResource: 'r', sourceRevision: '4', totalBytes: 42, expectedHeadVersion: 2, diverged: true });
    expect(plan.items[0].blockers).toEqual(['a.bin: file_not_text']);
    expect(plan.rejected).toHaveLength(1);
    expect(plan.skipped).toEqual([{ path: '.DS_Store', reason: 'housekeeping' }]);
  });
  it('does not invent historical text when the original source payload is absent', () => {
    const review = mapMigrationReview({ source_hub: { files: [{ path: 'SKILL.md', status: 'modified', from_text: 'source', to_text: 'hub' }] }, base_source: null, base_hub: null, basis_payload_available: false, has_draft: true });
    expect(review.sourceChanges).toBeUndefined();
    expect(review.hubChanges).toBeUndefined();
    expect(review.sourceHubChanges[0]).toMatchObject({ fromText: 'source', toText: 'hub' });
    expect(review.hasDraft).toBe(true);
  });
  it('preserves keep versus replace, CAS identity and explicit empty tags', () => {
    const input = { planId: 'plan', itemKey: 'one', expectedFingerprint: digest, expectedHeadVersion: 4, expectedHeadDigest: digest, mode: 'snapshot', rightsConfirmed: true };
    expect(migrationDecisionInput({ ...input, decision: 'keep' })).toMatchObject({ decision: 'keep', expected_head_version: 4, item_key: 'one' });
    expect(migrationDecisionInput({ ...input, tags: [] })).toMatchObject({ decision: 'replace', tags: [] });
    expect(migrationDecisionInput(input)).not.toHaveProperty('plan_id');
    expect(migrationDecisionInput({ ...input, decision: 'keep', rightsConfirmed: undefined }).rights_confirmed).toBe(false);
    expect(() => migrationDecisionInput({ ...input, decision: 'manual' })).toThrow();
  });
});
