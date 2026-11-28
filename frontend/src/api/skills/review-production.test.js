import { beforeEach, expect, it, vi } from 'vitest';
import axios from 'src/utils/axios';
import { loadSkillReview, mapRenewal, mapRequest } from './review-production';

vi.mock('src/utils/axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
const envelope = (result) => ({ data: { result } });
const packet = { candidate_version_id: 'version-4', candidate_digest: 'digest-4', transition: { expected_version: 3, version: 4, label: 'production' } };
const skill = { id: 'skill', labels: [{ name: 'production', can_move: true }], permissions: { can_manage_label_protection: true } };
const version = { id: 'version-4', number: 4, digest: 'digest-4', checks: { items: [] }, file_count: 1, files: [{ path: 'SKILL.md', sha256: 'hash' }] };
const change = { path: 'SKILL.md', status: 'modified', from_text: 'before', to_text: 'after' };
beforeEach(() => {
  vi.clearAllMocks();
  axios.get.mockImplementation(async (url) => {
    if (url.endsWith('/skill/')) return envelope(skill);
    if (url.endsWith('/versions/4/')) return envelope(version);
    if (url.endsWith('/compare/')) return envelope({ from: 3, to: 4, files: [change] });
    if (url.endsWith('/versions/4/files/')) return envelope({ path: 'SKILL.md', sha256: 'hash', size_bytes: 5, total_chars: 5, text: 'after', offset: 0, next_offset: null, eof: true });
    throw new Error(`Unexpected read ${url}`);
  });
});
it('compares the exact transition rather than latest and reads current label permissions', async () => {
  const result = await loadSkillReview('skill', packet, {});
  expect(result).toMatchObject({ complete: true, candidateDigest: 'digest-4', originChanges: [{ fromText: 'before', toText: 'after' }], releaseState: { permissions: { canPromote: true } } });
  expect(axios.get.mock.calls.find(([url]) => url.endsWith('/compare/'))[1].params).toEqual({ from: 3, to: 4 });
});
it('reads the full candidate against an empty baseline when the label is unassigned', async () => {
  const result = await loadSkillReview('skill', { ...packet, transition: { ...packet.transition, expected_version: null } }, {});
  expect(result.originChanges).toEqual([{ path: 'SKILL.md', status: 'added', fromText: '', toText: 'after' }]);
  expect(axios.get.mock.calls.some(([url]) => url.endsWith('/compare/'))).toBe(false);
});
it('rejects a comparison missing changed file bytes', async () => {
  const read = axios.get.getMockImplementation();
  axios.get.mockImplementation((url, config) => url.endsWith('/compare/') ? envelope({ from: 3, to: 4, files: [{ ...change, to_text: undefined }] }) : read(url, config));
  await expect(loadSkillReview('skill', packet, {})).rejects.toThrow('complete candidate comparison');
});
it('rejects a candidate whose immutable ID differs even with the same digest', async () => {
  await expect(loadSkillReview('skill', { ...packet, candidate_version_id: 'different-version' }, {})).rejects.toThrow('no longer matches');
});

// UXF-10: the renewal mapper carries the rule changes, and keeps "not sent" (null)
// apart from "nothing changed" ([]) so the dialog never claims nothing changed.
const renewal = { request_id: 'request', revision: 2, status: 'approved', subject_id: 'skill', previous_fingerprint: 'old', fingerprint: 'new',
  packet: { schema: 1, subject_kind: 'skill', subject_id: 'skill', candidate_version_id: 'version-4', candidate_digest: 'digest-4',
    transition: { label: 'production', version: 4, expected_version: 3, expected_release_revision: 2, expected_security_revision: 1, expected_impact_fingerprint: 'impact' },
    target: null, evidence: [], external_pipeline: null },
  changes: [{ item: 'transition.expected_security_revision', before: 0, after: 1 }], removed_evidence: [] };
it('maps policy changes with their values, who and when', () => {
  const mapped = mapRenewal({ ...renewal, policy_changes: [
    { field: 'protected_labels', before: [], after: ['production'], changed_by: 'Priya Shah', changed_at: '2026-11-16T10:00:00Z' },
    { field: 'security_rules', before: null, after: null, changed_by: null, changed_at: null },
  ] });
  expect(mapped.policyChanges).toEqual([
    { field: 'protected_labels', before: [], after: ['production'], changedBy: 'Priya Shah', changedAt: '2026-11-16T10:00:00Z', recorded: true },
    { field: 'security_rules', before: null, after: null, changedBy: null, changedAt: null, recorded: false },
  ]);
  expect(mapped.changes).toEqual([{ item: 'transition.expected_security_revision', before: 0, after: 1 }]);
});
it('keeps a missing policy list as null, not as no changes', () => {
  expect(mapRenewal(renewal).policyChanges).toBeNull();
  expect(mapRenewal({ ...renewal, policy_changes: [] }).policyChanges).toEqual([]);
});

it('keeps the recorded decision distinct from whether it still applies', () => {
  expect(mapRequest({ status: 'approved', renewal_required: true })).toMatchObject({ status: 'approved', renewalRequired: true });
  expect(mapRequest({ status: 'approved' }).renewalRequired).toBe(false);
});
