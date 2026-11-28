import { act, renderHook } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import useDraftSession from './useDraftSession';

const api = vi.hoisted(() => ({ save: vi.fn() }));
vi.mock('src/api/skills', () => ({ useSaveSkillDraft: () => ({ mutateAsync: api.save }), getSkillErrorCode: (error) => error.code }));
const initial = { files: [{ path: 'AGENTS.md', content: 'Retained policy' }], revision: 4, baseVersion: 2, note: '', description: 'Purpose', digest: 'compiled', recoveryDigest: 'recovery', expectedHeadVersion: 6, resolvedLock: { schema: 1, links: [{ skillId: 'child', version: 8 }] } };
const adapter = { saveDraft: api.save, saveInput: (draft, revision) => ({ files: draft.files, revision, baseVersion: draft.baseVersion, description: draft.description, note: draft.note }) };
beforeEach(() => api.save.mockReset());

it('preserves a clean revision through flush, head acknowledgment and review navigation', async () => {
  const { result } = renderHook(() => useDraftSession('context', initial, true, adapter));
  let acknowledged;
  await act(async () => { acknowledged = await result.current.flush(); });
  expect(acknowledged.revision).toBe(4);
  const acknowledge = vi.fn(async (draft) => ({ ...draft, revision: 5, reviewFingerprint: 'review-r5' }));
  await act(async () => { await result.current.mutateDraft(acknowledge); });
  await act(async () => { acknowledged = await result.current.flush(); });
  expect(acknowledge).toHaveBeenCalledWith(expect.objectContaining({ revision: 4 }));
  expect(acknowledged).toMatchObject({ revision: 5, reviewFingerprint: 'review-r5' });
  expect(api.save).not.toHaveBeenCalled();
});

it('uses authoritative saved digest/checks without replacing later local purpose edits', async () => {
  let finish;
  api.save.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  api.save.mockImplementationOnce(async (input) => ({ ...initial, ...input, revision: 6, digest: 'compiled-r6', recoveryDigest: 'recovery-r6', checks: { summary: { errors: 0 } } }));
  const { result } = renderHook(() => useDraftSession('context', initial, true, adapter));
  let first;
  act(() => { result.current.editFields({ note: 'Reviewed' }); first = result.current.save(); });
  act(() => result.current.editFields({ description: 'Typed during save' }));
  await act(async () => { finish({ ...initial, note: 'Reviewed', revision: 5, digest: 'compiled-r5', recoveryDigest: 'recovery-r5' }); await first; });
  expect(result.current.description).toBe('Typed during save');
  expect(result.current.dirty).toBe(true);
  await act(async () => { await result.current.flush(); });
  expect(result.current).toMatchObject({ revision: 6, digest: 'compiled-r6', recoveryDigest: 'recovery-r6', description: 'Typed during save', dirty: false });
});

it.each(['origin', 'lock'])('refuses an ordinary-save response that substitutes the accepted %s', async (change) => {
  api.save.mockImplementation(async (input) => ({ ...initial, ...input, revision: 5, ...(change === 'origin' ? { baseVersion: 6 } : { resolvedLock: { schema: 1, links: [] } }) }));
  const { result } = renderHook(() => useDraftSession('context', initial, true, adapter));
  act(() => result.current.editFields({ description: 'Local purpose' }));
  await act(async () => { expect(await result.current.flush()).toBeNull(); });
  expect(result.current).toMatchObject({ baseVersion: 2, revision: 4, description: 'Local purpose', dirty: true, status: 'error' });
  expect(result.current.resolvedLock).toEqual(initial.resolvedLock);
});

it('does not hydrate a dependency receipt as a draft or clear unsent work', async () => {
  const { result } = renderHook(() => useDraftSession('context', initial, true, adapter));
  await act(async () => { expect(await result.current.mutateDraft(async () => ({ contextId: 'context', revision: 5, digest: 'receipt' }))).toBeNull(); });
  expect(result.current.files).toEqual(initial.files);
  expect(result.current.revision).toBe(4);
  expect(await result.current.flush()).toBeNull();
});
