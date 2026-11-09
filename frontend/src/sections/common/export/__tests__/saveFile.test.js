import { afterEach, describe, expect, it, vi } from "vitest";
import { saveFile } from "../datasetExport";

vi.mock("src/utils/axios", () => ({ default: {}, endpoints: {} }));

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("download resource cleanup", () => {
  it("revokes the original object URL after the global window is gone", () => {
    const originalURL = window.URL;
    const revokeObjectURL = vi.fn();
    const createObjectURL = vi.fn((blob) => {
      expect(blob).toBeInstanceOf(Blob);
      expect(blob.type).toBe("text/csv");
      return "blob:download-test";
    });
    vi.stubGlobal(
      "URL",
      Object.assign(class extends originalURL {}, {
        createObjectURL,
        revokeObjectURL,
      }),
    );
    const resourceURL = window.URL;
    revokeObjectURL.mockImplementation(function revoke(url) {
      expect(this).toBe(resourceURL);
      expect(url).toBe("blob:download-test");
    });
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function click() {
        expect(this.getAttribute("download")).toBe("rows.csv");
        expect(this.href).toBe("blob:download-test");
        expect(document.body.contains(this)).toBe(true);
      });
    // The return value is unused; run the captured callback explicitly below.
    const schedule = vi.spyOn(window, "setTimeout").mockReturnValue(undefined);

    expect(saveFile({ data: "abc", mime: "text/csv", name: "rows.csv" })).toBe(
      3,
    );
    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(click).toHaveBeenCalledOnce();
    expect(document.querySelector('a[download="rows.csv"]')).toBeNull();
    expect(schedule).toHaveBeenCalledWith(expect.any(Function), 1000);
    expect(revokeObjectURL).not.toHaveBeenCalled();

    const cleanup = schedule.mock.calls[0][0];
    expect(typeof cleanup).toBe("function");
    if (typeof cleanup !== "function")
      throw new Error("Expected a cleanup callback");
    vi.stubGlobal("window", undefined);
    expect(() => cleanup()).not.toThrow();
    expect(revokeObjectURL).toHaveBeenCalledExactlyOnceWith(
      "blob:download-test",
    );
  });
});
