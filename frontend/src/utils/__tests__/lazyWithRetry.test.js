import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { retryImport, isChunkError } from "../lazyWithRetry.js";

const RELOAD_KEY = "chunk_reload_attempted";

describe("retryImport — resolved-to-undefined / missing-default recovery", () => {
  let reloadMock;
  let store;

  beforeEach(() => {
    store = {};
    vi.stubGlobal("sessionStorage", {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => {
        store[k] = String(v);
      },
      removeItem: (k) => {
        delete store[k];
      },
    });
    reloadMock = vi.fn();
    Object.defineProperty(window, "location", {
      value: { reload: reloadMock },
      writable: true,
      configurable: true,
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns the module and clears the reload flag on a valid import", async () => {
    store[RELOAD_KEY] = "1";
    const mod = { default: () => null };
    await expect(retryImport(() => Promise.resolve(mod), 3)).resolves.toBe(mod);
    expect(reloadMock).not.toHaveBeenCalled();
    expect(store[RELOAD_KEY]).toBeUndefined(); // flag cleared on success
  });

  it("does a one-time reload when the import resolves without a default export", async () => {
    const importFn = vi.fn().mockResolvedValue({ notDefault: 1 });
    // Recovery returns a never-resolving promise (keeps Suspense up while
    // reloading), so don't await it — just flush microtasks and assert.
    retryImport(importFn, 3);
    await new Promise((r) => setTimeout(r, 0));
    expect(importFn).toHaveBeenCalledTimes(1);
    expect(reloadMock).toHaveBeenCalledTimes(1);
    expect(store[RELOAD_KEY]).toBe("1");
  });

  it("does a one-time reload when the import resolves to undefined", async () => {
    retryImport(() => Promise.resolve(undefined), 3);
    await new Promise((r) => setTimeout(r, 0));
    expect(reloadMock).toHaveBeenCalledTimes(1);
  });

  it("throws a recognized chunk error (no second reload) if reload already attempted", async () => {
    store[RELOAD_KEY] = "1";
    await expect(
      retryImport(() => Promise.resolve({ noDefault: true }), 3),
    ).rejects.toThrow(/Failed to fetch dynamically imported module/);
    expect(reloadMock).not.toHaveBeenCalled();
  });

  it("retries a thrown chunk error, then succeeds", async () => {
    const mod = { default: () => null };
    let calls = 0;
    const importFn = vi.fn(() => {
      calls += 1;
      if (calls === 1)
        return Promise.reject(
          new Error("Failed to fetch dynamically imported module"),
        );
      return Promise.resolve(mod);
    });
    // retriesLeft high enough; backoff uses setTimeout — use fake timers.
    vi.useFakeTimers();
    const p = retryImport(importFn, 3);
    await vi.runAllTimersAsync();
    await expect(p).resolves.toBe(mod);
    expect(importFn).toHaveBeenCalledTimes(2);
    vi.useRealTimers();
  });
});

describe("isChunkError", () => {
  it("matches known chunk / dynamic-import failures", () => {
    expect(
      isChunkError(new Error("Failed to fetch dynamically imported module")),
    ).toBe(true);
    expect(isChunkError(new Error("Loading chunk 5 failed"))).toBe(true);
    expect(isChunkError({ name: "ChunkLoadError" })).toBe(true);
  });
  it("does not match unrelated errors or nullish input", () => {
    expect(isChunkError(new Error("Cannot read properties of null"))).toBe(
      false,
    );
    expect(isChunkError(null)).toBe(false);
    expect(isChunkError(undefined)).toBe(false);
  });
});
