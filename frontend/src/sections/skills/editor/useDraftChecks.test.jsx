import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import useDraftChecks from "./useDraftChecks";

const api = vi.hoisted(() => ({ checks: vi.fn() }));
vi.mock("src/api/skills", () => ({ useRunSkillChecks: () => ({ mutateAsync: api.checks }) }));
const files = [{ path: "SKILL.md", content: "Initial" }];
const changed = [{ path: "SKILL.md", content: "Changed" }];
const resultData = { summary: { errors: 0, warnings: 0, infos: 0 }, items: [] };
const advance = (time) => act(async () => { await vi.advanceTimersByTimeAsync(time); });
beforeEach(() => { vi.useFakeTimers(); api.checks.mockReset(); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("live draft checks", () => {
  it("debounces 600 ms from the last edit", async () => {
    api.checks.mockResolvedValue(resultData);
    const { result, rerender } = renderHook(({ value }) => useDraftChecks(value, "sample"), { initialProps: { value: files } });
    await advance(500);
    rerender({ value: changed });
    await advance(599);
    expect(api.checks).not.toHaveBeenCalled();
    expect(result.current.pending).toBe(true);
    await advance(1);
    expect(api.checks).toHaveBeenCalledExactlyOnceWith({ files: changed, name: "sample" });
    expect(result.current.checks).toEqual(resultData);
  });

  it("ignores an out-of-order response for obsolete files", async () => {
    let resolveOld;
    api.checks.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
    api.checks.mockResolvedValueOnce(resultData);
    const { result, rerender } = renderHook(({ value }) => useDraftChecks(value, "sample"), { initialProps: { value: files } });
    await advance(600);
    rerender({ value: changed });
    await advance(600);
    await act(async () => resolveOld({ summary: { errors: 1 }, items: [] }));
    expect(result.current.checks).toEqual(resultData);
    expect(result.current.pending).toBe(false);
  });
});

it("keeps previous findings without authorizing publication while new bytes are pending", async () => {
  api.checks.mockResolvedValueOnce(resultData);
  const { result, rerender } = renderHook(({ value }) => useDraftChecks(value, "sample"), { initialProps: { value: files } });
  await advance(600);
  expect(result.current.current).toBe(true);
  api.checks.mockReturnValue(new Promise(() => {}));
  rerender({ value: changed });
  expect(result.current.previous).toEqual(resultData);
  expect(result.current.checks).toBeNull();
  expect(result.current.current).toBe(false);
  expect(result.current.pending).toBe(true);
});

it("invalidates a same-file check when the skill name changes or a retry starts", async () => {
  api.checks.mockResolvedValue(resultData);
  const { result, rerender } = renderHook(({ name }) => useDraftChecks(files, name), { initialProps: { name: 'one' } });
  await advance(600);
  act(() => result.current.retry());
  expect(result.current.current).toBe(false);
  expect(result.current.previous).toEqual(resultData);
  await advance(600);
  rerender({ name: 'another' });
  expect(result.current.checks).toBeNull();
  expect(result.current.previous).toBeNull();
});
