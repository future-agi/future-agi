import { beforeEach, expect, it, vi } from 'vitest';
import axios from 'src/utils/axios';
import { githubPreviewInput, useCreateGitHubPreview, useStageSkillSourceUpdate, useGitHubPreview } from './github';
vi.mock('src/utils/axios', () => ({ default: { post: vi.fn() } }));
vi.mock('./endpoints', () => ({ endpoints: { githubPreviews: () => '/previews/', stageSourceUpdate: () => '/source/stage-update/' } }));
vi.mock('./testing', () => ({ useSkillV11Mutation: (options) => options, useSkillV11Query: (options) => options, idempotencyHeaders: (key) => ({ 'Idempotency-Key': key }), skillV11Result: (response) => response.data.result }));
beforeEach(() => vi.clearAllMocks());
it('continues a multi-root preview without converting its tracked branch to a pinned SHA', async () => {
  axios.post.mockResolvedValue({ data: { result: { id: 'child', status: 'queued' } } });
  await useCreateGitHubPreview().send({ idempotencyKey: 'key', parentPreviewId: 'parent', path: 'skills/example' }, { headers: {} });
  expect(axios.post.mock.calls[0][1]).toEqual({ parent_preview_id: 'parent', path: 'skills/example' });
});
it('rejects mixing continuation with a newly resolved source', () => {
  expect(() => githubPreviewInput({ parentPreviewId: 'parent', path: 'skill', ref: 'main' })).toThrow('continue');
});
it('preserves optional initial ref and path as null', () => {
  expect(githubPreviewInput({ url: 'https://github.com/example/synthetic' })).toEqual({ url: 'https://github.com/example/synthetic', path: null, ref: null });
});
it('stages only a reviewed unpublished replacement, retaining all CAS values', async () => {
  axios.post.mockResolvedValue({ data: { result: { draft: { revision: 1, files: [] }, published: false } } });
  const value = await useStageSkillSourceUpdate('skill').send({ previewId: 'preview', revision: 0, expectedSourceRevision: 4, expectedLatestVersion: 5, expectedLatestDigest: 'digest', acceptReplacement: false, rights: { confirmed: true, reviewNote: 'Reviewed' } }, {});
  expect(axios.post.mock.calls[0][1]).toEqual({ preview_id: 'preview', revision: 0, expected_source_revision: 4, expected_latest_version: 5, expected_latest_digest: 'digest', accept_replacement: false, rights: { confirmed: true, review_note: 'Reviewed' } });
  expect(value.published).toBe(false);
});
it('refreshes source checked-state when its update preview settles', () => {
  expect(useGitHubPreview('preview', 'skill').terminalParts).toEqual([['skill', 'source']]);
});
it('retains the immutable consumed receipt without inventing a draft on replay', async () => {
  axios.post.mockResolvedValue({ data: { result: { draft: null, published: false, consumed: { skill_id: 'skill', draft_revision: 7 } } } });
  const value = await useStageSkillSourceUpdate('skill').send({ previewId: 'preview', revision: 0, expectedSourceRevision: 4, expectedLatestVersion: 5, expectedLatestDigest: 'digest', acceptReplacement: false, rights: { confirmed: true, reviewNote: 'Reviewed' } }, {});
  expect(value).toEqual({ draft: null, published: false, consumed: { skillId: 'skill', draftRevision: 7 } });
});
