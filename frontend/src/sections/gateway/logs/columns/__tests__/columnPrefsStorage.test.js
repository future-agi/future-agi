import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  buildKey,
  readConfig,
  removeConfig,
  writeConfig,
} from "../columnPrefsStorage";
import { DEFAULT_CONFIG, MAX_COLUMN_ENTRIES } from "../columnModel";

const KEY_A = "agentcc.requestLogs.columns.v1:user-a:org-1:requests";

// Node 26 exposes an experimental `localStorage` global that is undefined
// without --localstorage-file, so tests stub a small in-memory Storage like the
// rest of the suite does (vi.stubGlobal("localStorage", ...)).
function createMemoryStorage() {
  const map = new Map();
  return {
    getItem: vi.fn((k) => (map.has(k) ? map.get(k) : null)),
    setItem: vi.fn((k, v) => {
      map.set(k, String(v));
    }),
    removeItem: vi.fn((k) => {
      map.delete(k);
    }),
    clear: vi.fn(() => map.clear()),
    key: vi.fn((i) => Array.from(map.keys())[i] ?? null),
    get length() {
      return map.size;
    },
  };
}

describe("buildKey (R13/R29)", () => {
  it("joins the versioned prefix with user, org and view ids", () => {
    expect(buildKey("user-a", "org-1", "requests")).toBe(KEY_A);
  });

  it.each([
    [null, "org-1"],
    ["user-a", null],
    ["", "org-1"],
    ["user-a", ""],
    [undefined, undefined],
  ])("returns null when any identity segment is missing (%s, %s)", (u, o) => {
    expect(buildKey(u, o, "requests")).toBeNull();
    expect(buildKey("user-a", "org-1", "")).toBeNull();
  });
});

describe("storage adapter", () => {
  let storage;

  beforeEach(() => {
    storage = createMemoryStorage();
    vi.stubGlobal("localStorage", storage);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("round-trips a valid record and rejects corrupt ones without rewriting them (AC12)", () => {
    expect(writeConfig(KEY_A, DEFAULT_CONFIG)).toEqual({ ok: true });
    expect(readConfig(KEY_A)).toEqual({ ok: true, config: DEFAULT_CONFIG });

    storage.setItem(KEY_A, "{not json");
    expect(readConfig(KEY_A)).toEqual({
      ok: false,
      config: null,
      reason: "invalid",
    });
    expect(storage.getItem(KEY_A)).toBe("{not json");

    storage.setItem(KEY_A, JSON.stringify({ v: 2, columns: [] }));
    expect(readConfig(KEY_A).config).toBeNull();
    expect(storage.getItem(KEY_A)).toBe(JSON.stringify({ v: 2, columns: [] }));
  });

  it.each([
    [
      "duplicate hidden ids",
      {
        v: 1,
        columns: DEFAULT_CONFIG.columns,
        hidden: ["builtin:provider", "builtin:provider"],
      },
    ],
    [
      "a 64-entry record lacking a built-in",
      {
        v: 1,
        columns: [
          ...DEFAULT_CONFIG.columns.slice(0, -1),
          ...Array.from({ length: MAX_COLUMN_ENTRIES - 9 }, (_, i) => ({
            id: `metadata:p${i}`,
          })),
        ],
        hidden: [],
      },
    ],
  ])(
    "rejects %s as invalid and leaves the stored bytes alone (AC12)",
    (_label, record) => {
      const bytes = JSON.stringify(record);
      storage.setItem(KEY_A, bytes);
      expect(readConfig(KEY_A)).toEqual({
        ok: false,
        config: null,
        reason: "invalid",
      });
      expect(storage.getItem(KEY_A)).toBe(bytes);
      expect(storage.setItem).toHaveBeenCalledTimes(1);
      expect(storage.removeItem).not.toHaveBeenCalled();
    },
  );

  it("returns defaults for an absent key and reports a null key as no-op", () => {
    expect(readConfig(KEY_A)).toEqual({ ok: true, config: null });
    expect(readConfig(null)).toEqual({
      ok: false,
      config: null,
      reason: "no-key",
    });
    expect(writeConfig(null, DEFAULT_CONFIG)).toEqual({
      ok: false,
      reason: "no-key",
    });
    expect(removeConfig(null)).toEqual({ ok: false, reason: "no-key" });
    expect(storage.setItem).not.toHaveBeenCalled();
    expect(storage.removeItem).not.toHaveBeenCalled();
  });

  it("removes only the given key (AC13)", () => {
    const keyB = buildKey("user-b", "org-1", "requests");
    writeConfig(KEY_A, DEFAULT_CONFIG);
    writeConfig(keyB, DEFAULT_CONFIG);
    const before = storage.getItem(keyB);
    expect(removeConfig(KEY_A)).toEqual({ ok: true });
    expect(storage.getItem(KEY_A)).toBeNull();
    expect(storage.getItem(keyB)).toBe(before);
  });

  it("reports quota and security failures instead of throwing (R16)", () => {
    storage.setItem.mockImplementation(() => {
      throw new DOMException("quota", "QuotaExceededError");
    });
    expect(writeConfig(KEY_A, DEFAULT_CONFIG)).toEqual({
      ok: false,
      reason: "write-failed",
    });
    storage.getItem.mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    expect(readConfig(KEY_A)).toEqual({
      ok: false,
      config: null,
      reason: "read-failed",
    });
    storage.removeItem.mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    expect(removeConfig(KEY_A)).toEqual({ ok: false, reason: "remove-failed" });
  });

  it("treats a blocked storage object as a read/write failure", () => {
    vi.stubGlobal("localStorage", undefined);
    expect(readConfig(KEY_A)).toEqual({
      ok: false,
      config: null,
      reason: "read-failed",
    });
    expect(writeConfig(KEY_A, DEFAULT_CONFIG)).toEqual({
      ok: false,
      reason: "write-failed",
    });
  });

  it("persists only ids, never labels or values (R13)", () => {
    writeConfig(KEY_A, {
      v: 1,
      columns: [{ id: "builtin:startedAt", label: "Timestamp", sample: "x" }],
      hidden: [],
    });
    expect(JSON.parse(storage.getItem(KEY_A))).toEqual({
      v: 1,
      columns: [
        { id: "builtin:startedAt" },
        { id: "builtin:model" },
        { id: "builtin:provider" },
        { id: "builtin:application" },
        { id: "builtin:service" },
        { id: "builtin:statusCode" },
        { id: "builtin:latencyMs" },
        { id: "builtin:cost" },
        { id: "builtin:totalTokens" },
        { id: "builtin:sessionId" },
      ],
      hidden: [],
    });
  });
});
