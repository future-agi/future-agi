import { describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { uploadsLabel, useFileUploads } from "../forms/shared/useFileUploads";

const uploadHarnessDesignDocument = vi.hoisted(() => vi.fn());
vi.mock("src/api/harness/releases", () => ({ uploadHarnessDesignDocument }));

const file = (name) => new File(["a,b\n1,2"], name, { lastModified: 1 });
const statuses = (result) =>
  Object.fromEntries(result.current.files.map((f) => [f.name, f.status]));

describe("useFileUploads", () => {
  it("uploads the rest past a failed file, and Retry recovers it", async () => {
    uploadHarnessDesignDocument
      .mockResolvedValueOnce({ id: "a84ffd6f-d21d-4121-944c-fa82dcc9b8d1" })
      .mockRejectedValueOnce(new Error("The upload was interrupted"))
      .mockRejectedValueOnce({ error: "document_unsupported" })
      .mockResolvedValueOnce({ id: "76469425-7b3d-40f9-8146-dd2f318345f0" });
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

    expect(uploadHarnessDesignDocument).toHaveBeenCalledTimes(3);
    await waitFor(() =>
      expect(statuses(result)).toEqual({
        "orders.csv": "done",
        "fail-refunds.csv": "failed",
        "notes.exe": "failed",
      }),
    );
    expect(result.current.done[0].documentId).toBe(
      "a84ffd6f-d21d-4121-944c-fa82dcc9b8d1",
    );
    expect(result.current.failed.map((f) => f.error)).toEqual([
      "upload_failed",
      "unsupported",
    ]);

    const failed = result.current.failed[0];
    act(() => result.current.retry(failed.id));
    expect(result.current.uploading).toHaveLength(1);
    // The failed notes.exe stays in the total but isn't counted as uploaded.
    expect(uploadsLabel(result.current)).toBe("Uploading 2 of 3…");
    await waitFor(() =>
      expect(statuses(result)["fail-refunds.csv"]).toBe("done"),
    );
    expect(result.current.done).toHaveLength(2);
    expect(result.current.done[1].documentId).toBe(
      "76469425-7b3d-40f9-8146-dd2f318345f0",
    );
  });
});
