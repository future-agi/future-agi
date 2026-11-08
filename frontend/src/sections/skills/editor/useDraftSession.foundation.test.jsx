import { act, renderHook } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';

import useDraftSession from './useDraftSession';

const api = vi.hoisted(() => ({ save: vi.fn() }));
vi.mock('src/api/skills', () => ({ useSaveSkillDraft: () => ({ mutateAsync: api.save }), getSkillErrorCode: (error) => error.code }));
const initial = { files: [{ path: 'AGENTS.md', content: 'Reviewed policy' }], revision: 4, baseVersion: 2, description: 'Initial purpose', note: '', resolvedLock: { links: ['v8'] } };
const resourceApi = { saveDraft: api.save, saveInput: (draft, revision) => ({ files: draft.files, description: draft.description, note: draft.note, baseVersion: draft.baseVersion, revision }) };

beforeEach(() => { api.save.mockReset(); });

it('flush includes purpose and note edits made after an in-flight file save', async () => {
  let finish;
  api.save.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  api.save.mockImplementationOnce(async (request) => ({ ...initial, ...request, revision: 6 }));
  const { result } = renderHook(() => useDraftSession('context', initial, true, resourceApi));
  let first;
  act(() => { result.current.editFields({ note: 'First edit' }); first = result.current.save(); });
  act(() => result.current.editFields({ description: 'New purpose', note: 'Second edit' }));
  let flushed;
  await act(async () => {
    const pending = result.current.flush();
    finish({ ...initial, note: 'First edit', revision: 5 });
    await first;
    flushed = await pending;
  });
  expect(api.save).toHaveBeenNthCalledWith(2, expect.objectContaining({ revision: 5, description: 'New purpose', note: 'Second edit' }));
  expect(flushed).toMatchObject({ revision: 6, description: 'New purpose' });
  expect(result.current.dirty).toBe(false);
});

it('orders accepted dependency writes between flushed authored snapshots', async () => {
  api.save.mockImplementation(async (request) => ({ ...initial, ...request, revision: request.revision + 1 }));
  let finishApply;
  const apply = vi.fn((draft) => new Promise((resolve) => { finishApply = () => resolve({ ...draft, resolvedLock: { links: ['v9'] }, revision: draft.revision + 1 }); }));
  const { result } = renderHook(() => useDraftSession('context', initial, true, resourceApi));
  let mutation;
  await act(async () => { mutation = result.current.mutateDraft(apply); });
  expect(result.current.dirty).toBe(true);
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({ revision: 5 }));
  act(() => result.current.editFields({ description: 'Typed during dependency apply' }));
  let flushed;
  await act(async () => {
    const pending = result.current.flush();
    finishApply();
    await mutation;
    flushed = await pending;
  });
  expect(api.save).toHaveBeenLastCalledWith(expect.objectContaining({ revision: 6, description: 'Typed during dependency apply' }));
  expect(flushed.revision).toBe(7);
  expect(result.current.resolvedLock).toEqual({ links: ['v9'] });
  expect(result.current.description).toBe('Typed during dependency apply');
  expect(result.current.dirty).toBe(false);
});

it('does not return runnable content after dependency conflict', async () => {
  api.save.mockImplementation(async (request) => ({ ...initial, ...request, revision: request.revision + 1 }));
  const { result } = renderHook(() => useDraftSession('context', initial, true, resourceApi));
  await act(async () => { await result.current.mutateDraft(async () => { throw { code: 'draft_conflict' }; }); });
  expect(await result.current.flush()).toBeNull();
  expect(result.current.resolvedLock).toEqual(initial.resolvedLock);
});

it('refuses local edits to server-owned accepted locks', () => {
  const { result } = renderHook(() => useDraftSession('context', initial, true, resourceApi));
  expect(() => result.current.editFields({ resolvedLock: { links: [] } })).toThrow('mutateDraft');
  expect(result.current.resolvedLock).toEqual(initial.resolvedLock);
});
