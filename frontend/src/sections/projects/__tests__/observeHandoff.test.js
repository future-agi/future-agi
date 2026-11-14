import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { KEEP_HANDOFFS, readHandoff, stashHandoff } from "../observeHandoff";

// A session storage holding at most `capacity` characters.
function boundedStorage(capacity = Infinity) {
  const items = new Map();
  const size = () =>
    [...items].reduce((n, [k, v]) => n + k.length + v.length, 0);
  return {
    get length() {
      return items.size;
    },
    key: (i) => [...items.keys()][i] ?? null,
    getItem: (key) => (items.has(key) ? items.get(key) : null),
    setItem: (key, value) => {
      const next = String(value);
      if (size() + key.length + next.length > capacity) {
        const error = new Error("QuotaExceededError");
        error.name = "QuotaExceededError";
        throw error;
      }
      items.set(key, next);
    },
    removeItem: (key) => items.delete(key),
  };
}

describe("hand-offs in session storage", () => {
  let now = 1_800_000_000_000;
  beforeEach(() => {
    vi.spyOn(Date, "now").mockImplementation(() => (now += 1000));
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("keeps the newest few, still readable on a reload", () => {
    vi.stubGlobal("sessionStorage", boundedStorage());
    const keys = Array.from({ length: KEEP_HANDOFFS + 5 }, (_, i) =>
      stashHandoff({ n: i }),
    );
    expect(sessionStorage.length).toBe(KEEP_HANDOFFS);
    expect(readHandoff(keys.at(-1))).toEqual({ n: KEEP_HANDOFFS + 4 });
    expect(readHandoff(keys.at(-1))).toEqual({ n: KEEP_HANDOFFS + 4 });
    expect(readHandoff(keys[0])).toBeNull();
  });

  it("makes room when the storage is full instead of failing every hand-off", () => {
    const ids = Array.from({ length: 300 }, (_, i) => `row-${i}`);
    vi.stubGlobal("sessionStorage", boundedStorage(12_000));
    let last;
    for (let i = 0; i < 6; i += 1) last = stashHandoff({ i, ids });
    expect(readHandoff(last)).toEqual({ i: 5, ids });
  });
});
