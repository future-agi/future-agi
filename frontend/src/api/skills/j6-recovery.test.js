import { beforeEach, expect, it, vi } from 'vitest';
import axios from 'src/utils/axios';
import { deriveCandidates, loadClosureReview, loadOpenContextRequests, loadRecovery, usePreviewRecoveryRelease } from './j6-recovery';

vi.mock('./testing', async (original) => ({ ...await original(), useSkillV11Mutation: (options) => options }));

vi.mock('src/utils/axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
const envelope = (result) => ({ data: { result } });
const hash = (character) => character.repeat(64);

it('keeps a quarantined release ineligible and the production release selectable only for local recovery', () => {
  const versions = [{ id: 'v3', number: 3 }, { id: 'v2', number: 2 }, { id: 'v1', number: 1 }];
  const row = (patch = {}) => ({ quarantined: false, distributionBlocked: false, ...patch });
  const result = deriveCandidates({ versions, productionVersionId: 'v3',
    rows: { v3: row(), v2: row({ quarantined: true }), v1: row() } });
  const byId = Object.fromEntries(result.map((candidate) => [candidate.id, candidate]));
  expect(byId.v1).toMatchObject({ eligible: true, selectable: true, lowerThanProduction: true });
  expect(byId.v2).toMatchObject({ eligible: false, selectable: false });
  expect(byId.v3).toMatchObject({ eligible: false, selectable: true, isProduction: true });
});

const manifest = (id, number, files) => ({ distribution_blocked: false, descriptor: { links: [] },
  manifest: { version_id: id, version: number, digest: `sha256:${id}`, file_count: files.length, file_offset: 0, next_file_offset: null, files } });

beforeEach(() => {
  vi.clearAllMocks();
  const files = { v1: { 'AGENTS.md': ['good\n', hash('a')], 'skills/x/SKILL.md': ['same\n', hash('c')] }, v3: { 'AGENTS.md': ['bad\nREFUND-WINDOW-DAYS: 7\n', hash('b')], 'skills/x/SKILL.md': ['same\n', hash('c')] } };
  axios.get.mockImplementation(async (url, config) => {
    const number = url.includes('/versions/1/') ? 1 : 3;
    const id = number === 1 ? 'v1' : 'v3';
    if (url.endsWith('/inspection/')) {
      return envelope(manifest(id, number, Object.entries(files[id]).map(([path, [text, sha256]]) => ({ path, sha256, size_bytes: text.length }))));
    }
    const [text, sha256] = files[id][config.params.path];
    return envelope({ version_id: id, digest: `sha256:${id}`, path: config.params.path, sha256, offset: 0, text, total_chars: text.length, next_offset: null, eof: true, revision: null });
  });
});

it('reads every file, but diffs only the files that differ from production', async () => {
  const review = await loadClosureReview({ contextId: 'ctx', candidate: { id: 'v1', number: 1 }, production: { id: 'v3', number: 3 } }, {});
  expect(review.rows.map((row) => [row.path, row.status])).toEqual([['AGENTS.md', 'changed'], ['skills/x/SKILL.md', 'unchanged']]);
  expect(review.changes).toHaveLength(1);
  expect(review.changes[0].lines.filter((line) => line.kind !== 'same').map((line) => `${line.kind}:${line.text}`))
    .toEqual(['removed:bad', 'removed:REFUND-WINDOW-DAYS: 7', 'added:good']);
});

it('refuses a blocked release instead of reviewing a partial closure', async () => {
  const read = axios.get.getMockImplementation();
  axios.get.mockImplementation(async (url, config) => {
    const value = await read(url, config);
    if (url.includes('/versions/1/') && url.endsWith('/inspection/')) value.data.result.distribution_blocked = true;
    return value;
  });
  await expect(loadClosureReview({ contextId: 'ctx', candidate: { id: 'v1', number: 1 }, production: { id: 'v3', number: 3 } }, {})).rejects.toThrow('blocked');
});

it('preserves selected evidence identity, annotation revision and external evidence in the preview payload', async () => {
  axios.post.mockResolvedValue(envelope({ packet: {}, fingerprint: 'fp' }));
  const external = { report_url: 'https://example.invalid/review' };
  await usePreviewRecoveryRelease().send({ contextId: 'ctx', candidateVersionId: 'v1', evidence: [{ source: 'skills_text', sourceId: 'run-1', annotationRevision: 7 }], external_pipeline: external }, {});
  expect(axios.post).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ candidate_version_id: 'v1', evidence: [{ source: 'skills_text', source_id: 'run-1', annotation_revision: 7 }], external_pipeline: external }), {});
});

it('fails closed without retrying a recovery inventory beyond the complete-review bound', async () => {
  axios.get.mockImplementation(async (url) => envelope(url.includes('release-state') ? { labels: [] } : { results: [], total_pages: url.includes('/versions/') ? 2 : 1 }));
  await expect(loadRecovery('ctx', {})).rejects.toMatchObject({ code: 'recovery_inventory_limit', message: "This context has more than 100 releases or consumers, which recovery can't review completely here." });
  expect(axios.get).toHaveBeenCalledTimes(3);
});

// UXF-08: the tracker resumes from the server's open requests, not from page state.
it("lists this person's open requests for the context with each one's transition and reviewer", async () => {
  const row = (id, subject, patch = {}) => ({ id, revision: 1, status: 'pending', subject_id: subject, subject_kind: 'agent_context', updated_at: '2026-11-17T10:00:00Z', ...patch });
  const detail = (id, patch = {}) => ({ id, revision: 1, status: 'approved', subject_id: 'ctx', recipients: [{ id: 'u2', name: 'Priya Shah' }],
    packet: { candidate_version_id: 'v1', transition: { label: 'production', expected_release_revision: 7 } }, ...patch });
  axios.get.mockImplementation(async (url, config) => {
    if (url.endsWith('/release-requests/')) {
      return config.params.status === 'pending'
        ? envelope({ results: [row('other', 'ctx-2'), row('skill', 'ctx', { subject_kind: 'skill' })], total_pages: 1 })
        : envelope({ results: [row('req-1', 'ctx', { status: 'approved', updated_at: '2026-11-17T11:00:00Z' }), row('stale', 'ctx')], total_pages: 1 });
    }
    if (url.endsWith('/req-1/')) return envelope(detail('req-1'));
    // A detail at another revision than its list row is a race: it is not resumed.
    if (url.endsWith('/stale/')) return envelope(detail('stale', { revision: 2 }));
    throw new Error(`unexpected ${url}`);
  });
  const requests = await loadOpenContextRequests('ctx', {});
  expect(requests).toEqual([{ requestId: 'req-1', requestRevision: 1, status: 'approved', candidateVersionId: 'v1', label: 'production', expectedReleaseRevision: 7, reviewer: 'Priya Shah' }]);
  expect(axios.get.mock.calls.filter(([url]) => url.endsWith('/release-requests/')).map(([, config]) => config.params.status)).toEqual(['pending', 'approved']);
});

it('treats a person who cannot write release requests as having none, and reports other failures', async () => {
  axios.get.mockRejectedValueOnce(Object.assign(new Error('denied'), { response: { status: 403 } }));
  await expect(loadOpenContextRequests('ctx', {})).resolves.toEqual([]);
  axios.get.mockRejectedValueOnce(Object.assign(new Error('down'), { response: { status: 503 } }));
  await expect(loadOpenContextRequests('ctx', {})).rejects.toThrow('down');
});
