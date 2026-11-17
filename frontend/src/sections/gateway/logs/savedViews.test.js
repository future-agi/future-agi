import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  deleteSavedView,
  loadSavedViews,
  saveView,
  SAVED_VIEWS_KEY,
} from "./savedViews";

beforeEach(() => window.localStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe("Saved filter persistence receipts", () => {
  it("reports successful save and delete only after storage accepts them", () => {
    const saved = saveView(" Errors ", "is_error=true&page=3&log=123");
    expect(saved).toEqual({
      views: [{ name: "Errors", query: "is_error=true" }],
      persisted: true,
      error: null,
    });
    expect(loadSavedViews()).toEqual(saved.views);
    expect(deleteSavedView("Errors")).toEqual({
      views: [],
      persisted: true,
      error: null,
    });
    expect(JSON.parse(window.localStorage.getItem(SAVED_VIEWS_KEY))).toEqual(
      [],
    );
  });

  it("returns usable in-memory views without pretending a failed save or delete persisted", () => {
    const original = saveView("Original", "model=original").views;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("Storage blocked");
    });
    const saved = saveView("Temporary", "provider=test", original);
    expect(saved.persisted).toBe(false);
    expect(saved.error).toBeTruthy();
    expect(saved.views).toEqual([
      ...original,
      { name: "Temporary", query: "provider=test" },
    ]);
    expect(loadSavedViews()).toEqual(original);
    const deleted = deleteSavedView("Original", saved.views);
    expect(deleted.persisted).toBe(false);
    expect(deleted.views).toEqual([
      { name: "Temporary", query: "provider=test" },
    ]);
    expect(loadSavedViews()).toEqual(original);
  });
});
