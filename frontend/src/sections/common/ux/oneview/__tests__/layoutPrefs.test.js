import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  emptyLayout,
  isEmptyLayout,
  layoutKey,
  mergeLayout,
  normalizeLayout,
  readLayout,
  removeLegacyModeKeys,
  writeLayout,
} from "../layoutPrefs";

const KEY = "fa.layout.u1.results.e1";

describe("layoutKey", () => {
  it("builds per user, surface and optional scope", () => {
    expect(layoutKey("u1", "results", "e1")).toBe(KEY);
    expect(layoutKey("u1", "list")).toBe("fa.layout.u1.list");
    expect(layoutKey(null, "list")).toBe("fa.layout.proto-user.list");
  });
});

describe("read / write", () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  it("missing or unparsable → the default layout", () => {
    expect(readLayout(KEY)).toEqual(emptyLayout());
    window.localStorage.setItem(KEY, "{not json");
    expect(readLayout(KEY)).toEqual(emptyLayout());
  });

  it("a version mismatch is ignored (default layout)", () => {
    window.localStorage.setItem(
      KEY,
      JSON.stringify({ v: 2, sections: { charts: false } }),
    );
    expect(readLayout(KEY)).toEqual(emptyLayout());
  });

  it("writes and reads back; an empty layout removes the key", () => {
    const value = mergeLayout(emptyLayout(), {
      sections: { charts: false },
      density: "tall",
    });
    expect(writeLayout(KEY, value)).toBe(true);
    expect(readLayout(KEY)).toEqual(value);
    expect(writeLayout(KEY, emptyLayout())).toBe(true);
    expect(window.localStorage.getItem(KEY)).toBe(null);
  });

  it("storage blocked: reads the default, writes report false, nothing throws", () => {
    vi.spyOn(window.localStorage, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    vi.spyOn(window.localStorage, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceeded");
    });
    expect(readLayout(KEY)).toEqual(emptyLayout());
    expect(writeLayout(KEY, { v: 1, density: "tall" })).toBe(false);
  });
});

describe("merge / normalize", () => {
  it("merges object fields one level deep; undefined removes a key", () => {
    const a = mergeLayout(emptyLayout(), {
      sections: { charts: false, frontier: true },
      extras: { why: true },
    });
    const b = mergeLayout(a, {
      sections: { charts: true, frontier: undefined },
    });
    expect(b.sections).toEqual({ charts: true });
    expect(b.extras).toEqual({ why: true });
    expect(mergeLayout(b, { density: "full" }).density).toBe("full");
    expect(mergeLayout(b, { density: null }).density).toBe(null);
  });

  it("replaces `columns` keys wholesale (column prefs are one object per key)", () => {
    const a = mergeLayout(emptyLayout(), {
      columns: { visible: { a: false }, order: ["b", "a"] },
    });
    const b = mergeLayout(a, { columns: { visible: { c: true } } });
    expect(b.columns).toEqual({ visible: { c: true }, order: ["b", "a"] });
  });

  it("normalizes garbage", () => {
    expect(normalizeLayout(null)).toEqual(emptyLayout());
    expect(normalizeLayout({ v: 1, sections: [], density: 4 })).toEqual(
      emptyLayout(),
    );
    expect(isEmptyLayout(emptyLayout())).toBe(true);
    expect(isEmptyLayout({ v: 1, extras: { x: 1 } })).toBe(false);
  });
});

describe("legacy Simple/Advanced key", () => {
  beforeEach(() => window.localStorage.clear());
  it("removes every fa.ui.mode.* key and nothing else", () => {
    window.localStorage.setItem("fa.ui.mode.u1", "advanced");
    window.localStorage.setItem("fa.ui.mode.proto-user", "simple");
    window.localStorage.setItem("fa.layout.u1.list", "{}");
    expect(removeLegacyModeKeys()).toBe(2);
    expect(window.localStorage.getItem("fa.ui.mode.u1")).toBe(null);
    expect(window.localStorage.getItem("fa.layout.u1.list")).toBe("{}");
    expect(removeLegacyModeKeys()).toBe(0);
  });
});
