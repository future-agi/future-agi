import { describe, it, expect } from "vitest";

import {
  applyUserVisibility,
  resolveColumnVisibility,
  mergeNonCustomColumns,
} from "../common";

describe("applyUserVisibility", () => {
  it("forces a non-default column visible when the user turned it on", () => {
    const col = { id: "total_tokens", isVisible: false };
    expect(applyUserVisibility(col, { total_tokens: true })).toEqual({
      id: "total_tokens",
      isVisible: true,
    });
  });

  it("leaves a non-default column hidden when the user did not enable it", () => {
    const col = { id: "user_id_type", isVisible: false };
    expect(applyUserVisibility(col, {})).toBe(col);
    expect(applyUserVisibility(col, { user_id_type: false })).toBe(col);
  });

  it("never overrides a default column (follows the backend)", () => {
    const col = { id: "duration", isVisible: false };
    expect(applyUserVisibility(col, { duration: true })).toBe(col);
  });

  it("passes through an already-visible column untouched", () => {
    const col = { id: "total_tokens", isVisible: true };
    expect(applyUserVisibility(col, { total_tokens: true })).toBe(col);
  });

  it("tolerates a missing updateObj", () => {
    const col = { id: "total_tokens", isVisible: false };
    expect(applyUserVisibility(col, undefined)).toBe(col);
  });
});

describe("resolveColumnVisibility (dropdown checkbox state)", () => {
  it("shows a backend-hidden non-default column as unchecked with no local override", () => {
    // Regression guard: pre-fix `updateObj[id] ?? true` wrongly returned true.
    const col = { id: "total_tokens", isVisible: false };
    expect(resolveColumnVisibility(col, {})).toBe(false);
  });

  it("lets a local override win over the backend value", () => {
    const col = { id: "total_tokens", isVisible: false };
    expect(resolveColumnVisibility(col, { total_tokens: true })).toBe(true);
  });

  it("falls back to the backend value, then to visible", () => {
    expect(resolveColumnVisibility({ id: "user_id", isVisible: true }, {})).toBe(
      true,
    );
    expect(resolveColumnVisibility({ id: "unknown" }, {})).toBe(true);
  });
});

describe("mergeNonCustomColumns", () => {
  it("keeps a user-shown non-default column visible on a fresh load (empty current)", () => {
    // Root cause A: fresh load routes every column through the `added` branch.
    const incoming = [
      { id: "session_id", isVisible: true },
      { id: "total_tokens", isVisible: false },
    ];
    const merged = mergeNonCustomColumns([], incoming, { total_tokens: true });
    expect(merged.find((c) => c.id === "total_tokens").isVisible).toBe(true);
    expect(merged.find((c) => c.id === "session_id").isVisible).toBe(true);
  });

  it("does not resurrect a non-default column the user never enabled", () => {
    const incoming = [{ id: "user_id_hash", isVisible: false }];
    const merged = mergeNonCustomColumns([], incoming, {});
    expect(merged[0].isVisible).toBe(false);
  });

  it("preserves visibility for kept columns and appends new ones", () => {
    const current = [{ id: "session_id", isVisible: true }];
    const incoming = [
      { id: "session_id", isVisible: true },
      { id: "total_tokens", isVisible: false },
    ];
    const merged = mergeNonCustomColumns(current, incoming, {
      total_tokens: true,
    });
    expect(merged.map((c) => c.id)).toEqual(["session_id", "total_tokens"]);
    expect(merged[1].isVisible).toBe(true);
  });
});
