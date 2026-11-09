import { afterEach, describe, expect, it, vi } from "vitest";

import { formatLogTime, localInputToIso, toLocalInput } from "../logDates";
import { statusOf } from "../logStatus";
import {
  deleteSavedView,
  loadSavedViews,
  saveView,
  SAVED_VIEWS_KEY,
} from "../savedViews";
import { exportErrorMessage } from "../exportLogs";

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("log dates", () => {
  it("sends the picked local time as an ISO instant with its zone", () => {
    // The input has no zone; the browser's zone applies, and the URL and the
    // API get an instant, so a 10:30 local pick is 10:30 in the table too.
    const local = "2026-10-20T10:30";
    const iso = localInputToIso(local);
    expect(iso).toBe(new Date(2026, 9, 20, 10, 30).toISOString());
    expect(iso.endsWith("Z")).toBe(true);
  });

  it("shows an ISO instant in the local datetime-local form", () => {
    const iso = new Date(2026, 9, 20, 10, 30).toISOString();
    expect(toLocalInput(iso)).toBe("2026-10-20T10:30");
  });

  it("reads an old zone-less link as local time, and ignores junk", () => {
    expect(toLocalInput("2026-10-20T10:30")).toBe("2026-10-20T10:30");
    expect(localInputToIso("")).toBe("");
    expect(localInputToIso("not a date")).toBe("");
    expect(toLocalInput("nope")).toBe("");
  });

  it("formats a log time to the millisecond", () => {
    const iso = new Date(2026, 9, 20, 15, 40, 5, 514).toISOString();
    expect(formatLogTime(iso)).toMatch(/Oct 20.*15:40:05\.514/);
    expect(formatLogTime(null)).toBe("—");
  });
});

describe("statusOf", () => {
  it.each([
    [{ status_code: 200 }, "healthy", "200"],
    [{ status_code: 204 }, "healthy", "204"],
    [{ status_code: 429 }, "warning", "429"],
    [{ status_code: 404 }, "warning", "404"],
    [{ status_code: 502 }, "error", "502"],
    [{ status_code: null }, "neutral", "—"],
  ])("tones %o as %s", (log, tone, label) => {
    expect(statusOf(log)).toMatchObject({ tone, label });
  });

  it("calls a 403 a guardrail block only when a guardrail triggered", () => {
    expect(
      statusOf({ status_code: 403, guardrail_triggered: true }),
    ).toMatchObject({ tone: "error", label: "403", blocked: true });
    expect(
      statusOf({ status_code: 403, guardrail_triggered: false }),
    ).toMatchObject({ tone: "warning", label: "403", blocked: false });
  });
});

describe("saved filters (this browser only)", () => {
  it("saves, lists and deletes named filter sets", () => {
    expect(loadSavedViews()).toEqual([]);
    saveView("Errors on checkout", "is_error=true&application=checkout");
    saveView("Slow", "min_latency=1000");
    expect(loadSavedViews().map((v) => v.name)).toEqual([
      "Errors on checkout",
      "Slow",
    ]);
    // Saving an existing name replaces it.
    saveView("Slow", "min_latency=2000");
    expect(loadSavedViews()).toHaveLength(2);
    expect(loadSavedViews()[1].query).toBe("min_latency=2000");
    deleteSavedView("Slow");
    expect(loadSavedViews().map((v) => v.name)).toEqual(["Errors on checkout"]);
  });

  it("drops page and sort-only noise and keeps working when storage throws", () => {
    saveView("Errors", "is_error=true&page=4&log=abc");
    expect(loadSavedViews()[0].query).toBe("is_error=true");
    window.localStorage.setItem(SAVED_VIEWS_KEY, "{broken");
    expect(loadSavedViews()).toEqual([]);
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(loadSavedViews()).toEqual([]);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(() => saveView("x", "a=1")).not.toThrow();
  });
});

describe("export errors", () => {
  it("shows the server's message from a blob error body", async () => {
    const blob = new Blob(
      [
        JSON.stringify({
          status: false,
          result:
            "Export limited to 10,000 rows. Current filter returns 12,000 rows. Please narrow your filters.",
        }),
      ],
      { type: "application/json" },
    );
    await expect(
      exportErrorMessage({ response: { data: blob } }),
    ).resolves.toBe(
      "Export limited to 10,000 rows. Current filter returns 12,000 rows. Please narrow your filters.",
    );
  });

  it("reads message and detail shapes and falls back to a sentence", async () => {
    await expect(
      exportErrorMessage({
        response: { data: { message: "No data to export" } },
      }),
    ).resolves.toBe("No data to export");
    await expect(exportErrorMessage({ detail: "Forbidden" })).resolves.toBe(
      "Forbidden",
    );
    await expect(exportErrorMessage(new Error(""))).resolves.toBe(
      "The export could not be made. Try again.",
    );
  });
});
