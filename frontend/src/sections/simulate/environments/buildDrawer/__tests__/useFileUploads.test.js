import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useFileUploads } from "../forms/shared/useFileUploads";

const file = (name) => new File(["a,b\n1,2"], name, { lastModified: 1 });
const statuses = (result) =>
  Object.fromEntries(result.current.files.map((f) => [f.name, f.status]));

describe("useFileUploads", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("uploads the rest past a failed file, and Retry recovers it", async () => {
    const { result } = renderHook(() => useFileUploads());
    act(() =>
      result.current.add([
        file("orders.csv"),
        file("fail-refunds.csv"),
        file("notes.exe"),
        file("orders.csv"),
      ]),
    );
    // The identical re-pick of orders.csv is skipped.
    expect(result.current.files).toHaveLength(3);
    expect(result.current.uploading.length).toBeGreaterThan(0);

    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(statuses(result)).toEqual({
      "orders.csv": "done",
      "fail-refunds.csv": "failed",
      "notes.exe": "failed",
    });
    expect(result.current.done[0].documentId).toMatch(/^doc-/);
    expect(result.current.failed.map((f) => f.error)).toEqual([
      "upload_failed",
      "unsupported",
    ]);

    const failed = result.current.failed[0];
    act(() => result.current.retry(failed.id));
    expect(result.current.uploading).toHaveLength(1);
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(statuses(result)["fail-refunds.csv"]).toBe("done");
    expect(result.current.done).toHaveLength(2);
  });
});
