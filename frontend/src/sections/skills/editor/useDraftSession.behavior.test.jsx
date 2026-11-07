import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import useDraftSession from "./useDraftSession";

const api = vi.hoisted(() => ({ save: vi.fn() }));
vi.mock("src/api/skills", () => ({
  useSaveSkillDraft: () => ({ mutateAsync: api.save }),
  getSkillErrorCode: (error) => error.code,
}));

const files = [{ path: "SKILL.md", content: "Initial instructions" }];
const initial = { files, revision: 4, baseVersion: 2, note: "A saved draft" };
const changed = [{ path: "SKILL.md", content: "Updated instructions" }];
const newer = [{ path: "SKILL.md", content: "More recent instructions" }];
const advance = async (milliseconds) => act(async () => { await vi.advanceTimersByTimeAsync(milliseconds); });

beforeEach(() => { vi.useFakeTimers(); api.save.mockReset(); });
afterEach(() => { vi.useRealTimers(); });

describe("draft save concurrency", () => {
  it("debounces the last edit for one second and saves with the current revision", async () => {
    api.save.mockResolvedValue({ ...initial, files: newer, revision: 5 });
    const { result } = renderHook(() => useDraftSession("skill-id", initial));
    act(() => result.current.edit(changed));
    await advance(600);
    act(() => result.current.edit(newer));
    await advance(999);
    expect(api.save).not.toHaveBeenCalled();
    await advance(1);
    expect(api.save).toHaveBeenCalledExactlyOnceWith({ files: newer, note: initial.note, baseVersion: 2, revision: 4 });
    expect(result.current.revision).toBe(5);
    expect(result.current.dirty).toBe(false);
  });

  it("does not save a newly hydrated draft until the first edit or explicit save", async () => {
    api.save.mockResolvedValue({ ...initial, revision: 1 });
    const { result } = renderHook(() => useDraftSession("skill-id", { ...initial, revision: 0 }));
    await advance(2000);
    expect(api.save).not.toHaveBeenCalled();
    await act(async () => { await result.current.save(); });
    expect(api.save).toHaveBeenCalledWith(expect.objectContaining({ revision: 0 }));
  });

  it("retains edits made during a save and sends them with the returned revision", async () => {
    let resolveFirst;
    api.save.mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }));
    api.save.mockResolvedValueOnce({ ...initial, files: newer, revision: 6 });
    const { result } = renderHook(() => useDraftSession("skill-id", initial));
    act(() => result.current.edit(changed));
    await advance(1000);
    act(() => result.current.edit(newer));
    await advance(2000);
    expect(api.save).toHaveBeenCalledTimes(1);
    await act(async () => { resolveFirst({ ...initial, files: changed, revision: 5 }); });
    expect(result.current.files).toBe(newer);
    expect(result.current.dirty).toBe(true);
    await advance(1000);
    expect(api.save).toHaveBeenNthCalledWith(2, { files: newer, note: initial.note, baseVersion: 2, revision: 5 });
    expect(result.current.dirty).toBe(false);
  });

  it("stops automatic retry after a conflict and overwrites only with an explicit server revision", async () => {
    api.save.mockRejectedValueOnce({ code: "draft_conflict", detail: { revision: 9 } });
    api.save.mockResolvedValueOnce({ ...initial, files: changed, revision: 10 });
    const { result } = renderHook(() => useDraftSession("skill-id", initial));
    act(() => result.current.edit(changed));
    await advance(1000);
    expect(result.current.status).toBe("conflict");
    await advance(5000);
    expect(api.save).toHaveBeenCalledTimes(1);
    await act(async () => { await result.current.save(9); });
    expect(api.save).toHaveBeenNthCalledWith(2, expect.objectContaining({ files: changed, revision: 9 }));
    expect(result.current.revision).toBe(10);
    expect(result.current.dirty).toBe(false);
  });

  it("reloads the server snapshot without scheduling a save", async () => {
    const { result } = renderHook(() => useDraftSession("skill-id", initial));
    act(() => result.current.edit(changed));
    act(() => result.current.reload({ ...initial, files: newer, revision: 8 }));
    await advance(1500);
    expect(result.current.files).toBe(newer);
    expect(result.current.revision).toBe(8);
    expect(result.current.dirty).toBe(false);
    expect(api.save).not.toHaveBeenCalled();
  });

  it("keeps failed saves dirty and permits an explicit retry", async () => {
    api.save.mockRejectedValueOnce({ code: "network_error" });
    api.save.mockResolvedValueOnce({ ...initial, files: changed, revision: 5 });
    const { result } = renderHook(() => useDraftSession("skill-id", initial));
    act(() => result.current.edit(changed));
    await advance(1000);
    expect(result.current.status).toBe("error");
    expect(result.current.dirty).toBe(true);
    await advance(5000);
    expect(api.save).toHaveBeenCalledTimes(1);
    await act(async () => { await result.current.save(); });
    expect(result.current.status).toBe("saved");
    expect(result.current.dirty).toBe(false);
  });
});
