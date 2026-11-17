import { runInNewContext } from "node:vm";
import html from "../../../index.html?raw";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { retryImport, requestChunkReload } from "../lazyWithRetry.js";

// Exercise the actual HTML listener on the baseline as well as the lazy owner.
// Vite returns undefined when a preloadError listener prevents the exception.
const legacyScripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)]
  .map((match) => match[1])
  .filter((script) => script.includes("vite:preloadError"));
const chunkFailure = () =>
  new TypeError("Failed to fetch dynamically imported module");

function failPreload(error) {
  const event = new Event("vite:preloadError", { cancelable: true });
  event.payload = error;
  window.dispatchEvent(event);
  if (!event.defaultPrevented) throw error;
  return undefined;
}

function pendingImport() {
  let reject;
  const promise = new Promise((_resolve, fail) => {
    reject = fail;
  });
  return { promise, reject };
}

describe("preload failures belong to the requesting route", () => {
  let reload;
  let location;
  let listeners;
  let storage;
  beforeEach(() => {
    storage = new Map();
    vi.stubGlobal("sessionStorage", {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, String(value)),
      removeItem: (key) => storage.delete(key),
    });
    reload = vi.fn();
    location = { pathname: "/route-a", search: "", hash: "", reload };
    Object.defineProperty(window, "location", {
      value: location,
      configurable: true,
      writable: true,
    });
    delete window.__RELOADING_FOR_VITE_PRELOAD_ERROR;
    window.dispatchEvent(new Event("pageshow"));
    listeners = [];
    legacyScripts.forEach((script) =>
      runInNewContext(script, {
        window: {
          location,
          addEventListener: (name, listener) => {
            listeners.push([name, listener]);
            window.addEventListener(name, listener);
          },
          set __RELOADING_FOR_VITE_PRELOAD_ERROR(value) {
            window.__RELOADING_FOR_VITE_PRELOAD_ERROR = value;
          },
        },
      }),
    );
  });
  afterEach(() => {
    listeners.forEach(([name, listener]) =>
      window.removeEventListener(name, listener),
    );
    window.dispatchEvent(new Event("pageshow"));
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("does not reload route B when route A's cancelled preload fails", async () => {
    const pending = pendingImport();
    const error = chunkFailure();
    const result = retryImport(() => pending.promise.catch(failPreload), 0);
    const rejected = expect(result).rejects.toBe(error);
    location.pathname = "/route-b";
    pending.reject(error);
    await rejected;
    expect(reload).not.toHaveBeenCalled();
    expect(window.__RELOADING_FOR_VITE_PRELOAD_ERROR).toBeUndefined();
    // The app's error boundary must not revive recovery for the abandoned owner.
    expect(requestChunkReload(error)).toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });

  it("does not cancel an outgoing document navigation before its URL commits", async () => {
    const pending = pendingImport();
    const error = new TypeError("error loading dynamically imported module");
    const result = retryImport(() => pending.promise.catch(failPreload), 0);
    const rejected = expect(result).rejects.toBe(error);
    window.dispatchEvent(new Event("beforeunload", { cancelable: true }));
    pending.reject(error);
    await rejected;
    expect(reload).not.toHaveBeenCalled();
    expect(window.__RELOADING_FOR_VITE_PRELOAD_ERROR).toBeUndefined();
  });

  it("reloads exactly once for a failed chunk still needed by the current route", async () => {
    retryImport(
      () => Promise.resolve().then(() => failPreload(chunkFailure())),
      0,
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(reload).toHaveBeenCalledTimes(1);
    expect(window.__RELOADING_FOR_VITE_PRELOAD_ERROR).toBe(true);
  });

  it("surfaces the second failure after reload instead of looping or swallowing it", async () => {
    retryImport(
      () => Promise.resolve().then(() => failPreload(chunkFailure())),
      0,
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
    reload.mockClear();
    delete window.__RELOADING_FOR_VITE_PRELOAD_ERROR;
    window.dispatchEvent(new Event("pageshow"));
    const error = chunkFailure();
    await expect(
      retryImport(() => Promise.resolve().then(() => failPreload(error)), 0),
    ).rejects.toBe(error);
    expect(requestChunkReload(error)).toBe(false);
    expect(reload).not.toHaveBeenCalled();
    expect(window.__RELOADING_FOR_VITE_PRELOAD_ERROR).toBeUndefined();
  });

  it("never reloads a named abort even if the URL has not changed", async () => {
    const error = new DOMException("The operation was aborted", "AbortError");
    await expect(retryImport(() => Promise.reject(error), 0)).rejects.toBe(
      error,
    );
    expect(requestChunkReload(error)).toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });

  it("does not retry or reload after navigation during the retry delay", async () => {
    vi.useFakeTimers();
    const error = chunkFailure();
    const importer = vi.fn().mockRejectedValue(error);
    const result = retryImport(importer, 2);
    const rejected = expect(result).rejects.toBe(error);
    await vi.advanceTimersByTimeAsync(0);
    location.pathname = "/route-b";
    await vi.runAllTimersAsync();
    await rejected;
    expect(importer).toHaveBeenCalledTimes(1);
    expect(reload).not.toHaveBeenCalled();
  });

  it("keeps the guard when another route recovers before returning to route A", () => {
    expect(requestChunkReload()).toBe(true);
    location.pathname = "/route-b";
    expect(requestChunkReload()).toBe(true);
    location.pathname = "/route-a";
    expect(requestChunkReload()).toBe(false);
    expect(reload).toHaveBeenCalledTimes(2);
  });

  it("recognizes Firefox's current-route chunk failure", async () => {
    retryImport(
      () =>
        Promise.reject(
          new TypeError("error loading dynamically imported module"),
        ),
      0,
    ).catch(() => {}); // Baseline rejects: the reload assertion below must fail.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(reload).toHaveBeenCalledTimes(1);
    expect(window.__RELOADING_FOR_VITE_PRELOAD_ERROR).toBe(true);
  });

  it("does not recover a pending import after its route's query changes", async () => {
    const pending = pendingImport();
    const result = retryImport(() => pending.promise, 0);
    const error = chunkFailure();
    const rejected = expect(result).rejects.toBe(error);
    location.search = "?other-route-state=1";
    pending.reject(error);
    await rejected;
    expect(reload).not.toHaveBeenCalled();
  });

  it("still recovers after a prevented beforeunload or a bfcache restore", async () => {
    const prevent = (event) => event.preventDefault();
    window.addEventListener("beforeunload", prevent);
    window.dispatchEvent(new Event("beforeunload", { cancelable: true }));
    window.removeEventListener("beforeunload", prevent);
    await Promise.resolve();
    expect(requestChunkReload()).toBe(true);
    location.pathname = "/route-b";
    window.dispatchEvent(new Event("pagehide"));
    expect(requestChunkReload()).toBe(false);
    window.dispatchEvent(new Event("pageshow"));
    expect(requestChunkReload()).toBe(true);
  });

  it("keys recovery by the app entry build, not the early recovery module", () => {
    const recovery = document.createElement("script");
    recovery.type = "module";
    recovery.src = "/assets/recovery-fixed.js";
    const entry = document.createElement("script");
    entry.type = "module";
    entry.src = "/assets/main-build-a.js";
    document.head.append(recovery, entry);
    try {
      expect(requestChunkReload()).toBe(true);
      expect(requestChunkReload()).toBe(false);
      entry.src = "/assets/main-build-b.js";
      expect(requestChunkReload()).toBe(true);
      expect(requestChunkReload()).toBe(false);
    } finally {
      recovery.remove();
      entry.remove();
    }
  });

  it("fails closed when reading the session guard throws", () => {
    sessionStorage.getItem = () => {
      throw new Error("Storage unavailable");
    };
    expect(requestChunkReload()).toBe(false);
    expect(reload).not.toHaveBeenCalled();
    expect(window.__RELOADING_FOR_VITE_PRELOAD_ERROR).toBeUndefined();
  });

  it("fails closed when the reload guard cannot be persisted", async () => {
    sessionStorage.setItem = () => {
      throw new Error("Storage unavailable");
    };
    const error = chunkFailure();
    await expect(retryImport(() => Promise.reject(error), 0)).rejects.toBe(
      error,
    );
    expect(reload).not.toHaveBeenCalled();
    expect(window.__RELOADING_FOR_VITE_PRELOAD_ERROR).toBeUndefined();
  });
});
