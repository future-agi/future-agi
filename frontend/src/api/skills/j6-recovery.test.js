import { beforeEach, expect, it, vi } from 'vitest';
import axios from 'src/utils/axios';
import { deriveCandidates, loadClosureReview, loadRecovery, usePreviewRecoveryRelease } from './j6-recovery';

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
