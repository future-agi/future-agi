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
  let serverLock = initial.resolvedLock;
  api.save.mockImplementation(async (request) => ({ ...initial, ...request, resolvedLock: serverLock, revision: request.revision + 1 }));
  let finishApply;
  const apply = vi.fn((draft) => new Promise((resolve) => { finishApply = () => { serverLock = { links: ['v9'] }; resolve({ ...draft, resolvedLock: serverLock, revision: draft.revision + 1 }); }; }));
  const { result } = renderHook(() => useDraftSession('context', initial, true, resourceApi));
  let mutation;
  await act(async () => { mutation = result.current.mutateDraft(apply); });
  expect(result.current.dirty).toBe(true);
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({ revision: 4 }));
  act(() => result.current.editFields({ description: 'Typed during dependency apply' }));
  let flushed;
  await act(async () => {
    const pending = result.current.flush();
    finishApply();
    await mutation;
    flushed = await pending;
  });
  expect(api.save).toHaveBeenLastCalledWith(expect.objectContaining({ revision: 5, description: 'Typed during dependency apply' }));
  expect(flushed.revision).toBe(6);
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

it('keeps an omitted mutation blocking after its prerequisite save fails', async () => {
  let rejectSave;
  api.save.mockImplementationOnce(() => new Promise((resolve, reject) => { rejectSave = reject; }));
  api.save.mockImplementation(async (request) => ({ ...initial, ...request, revision: request.revision + 1 }));
  const apply = vi.fn(async (draft) => ({ ...draft, resolvedLock: { links: ['v9'] }, revision: draft.revision + 1 }));
  const { result } = renderHook(() => useDraftSession('context', initial, true, resourceApi));
  let mutation;
  let flushing;
  await act(async () => {
    result.current.editFields({ note: 'Needs dependency update' });
    mutation = result.current.mutateDraft(apply);
    flushing = result.current.flush();
  });
  await act(async () => {
    rejectSave(new Error('Synthetic transient failure'));
    expect(await mutation).toBeNull();
    expect(await flushing).toBeNull();
  });
  expect(apply).not.toHaveBeenCalled();
  expect(result.current.mutationError).toBeTruthy();
  // Even a successful explicit ordinary save cannot acknowledge the lost update.
  await act(async () => { await result.current.save(); });
  expect(await result.current.flush()).toBeNull();
  expect(result.current.dirty).toBe(true);
  expect(result.current.resolvedLock).toEqual(initial.resolvedLock);
  await act(async () => { await result.current.mutateDraft(apply); });
  await act(async () => { expect(await result.current.flush()).not.toBeNull(); });
  expect(apply).toHaveBeenCalledTimes(1);
  expect(result.current.resolvedLock).toEqual({ links: ['v9'] });
  expect(result.current.dirty).toBe(false);
});

it('refuses local edits to server-owned accepted locks', () => {
  const { result } = renderHook(() => useDraftSession('context', initial, true, resourceApi));
  expect(() => result.current.editFields({ resolvedLock: { links: [] } })).toThrow('mutateDraft');
  expect(result.current.resolvedLock).toEqual(initial.resolvedLock);
});


it('does not replace an uncertain mutation with a different operation', async () => {
  const apply = vi.fn().mockRejectedValueOnce(new Error('Lost receipt')).mockResolvedValueOnce({ ...initial, revision: 5 });
  const other = vi.fn();
  const { result } = renderHook(() => useDraftSession('context', initial, true, resourceApi));
  await act(async () => { await result.current.mutateDraft(apply, { operationKey: 'original' }); });
  await act(async () => { expect(await result.current.mutateDraft(other, { operationKey: 'different' })).toBeNull(); });
  expect(other).not.toHaveBeenCalled();
  expect(result.current.mutationError).toBeTruthy();
  await act(async () => { await result.current.mutateDraft(other, { operationKey: 'original' }); });
  expect(other).not.toHaveBeenCalled();
  expect(apply).toHaveBeenCalledTimes(2);
  expect(apply.mock.calls[1]).toEqual(apply.mock.calls[0]);
});

it('serializes different queued mutations without replaying the first callback', async () => {
  let finish;
  const first = vi.fn((draft) => new Promise((resolve) => { finish = () => resolve({ ...draft, revision: 5 }); }));
  const second = vi.fn(async (draft) => ({ ...draft, revision: 6 }));
  const { result } = renderHook(() => useDraftSession('context', initial, true, resourceApi));
  let pending;
  await act(async () => { pending = result.current.mutateDraft(first, { operationKey: 'one' }); });
  await act(async () => {
    const queued = result.current.mutateDraft(second, { operationKey: 'two' });
    finish();
    await pending;
    await queued;
  });
  expect(first).toHaveBeenCalledOnce();
  expect(second).toHaveBeenCalledWith(expect.objectContaining({ revision: 5 }));
  expect(result.current.revision).toBe(6);
});
