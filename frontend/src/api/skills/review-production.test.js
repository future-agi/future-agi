import { beforeEach, expect, it, vi } from 'vitest';
import axios from 'src/utils/axios';
import { loadSkillReview } from './review-production';

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
