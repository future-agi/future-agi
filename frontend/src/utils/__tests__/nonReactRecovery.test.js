import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPdfjsLoader } from "../../sections/common/Values/pdfjsLoader";
import { importWithRecovery, requestChunkReload } from "../chunkRecovery";

const chunkFailure = () =>
  new TypeError(
    "Failed to fetch dynamically imported module: /assets/pdf-old.js",
  );
const modules = () => [{ GlobalWorkerOptions: {} }, { default: "/worker.js" }];
function pendingImport() {
  let reject;
  const promise = new Promise((_resolve, fail) => {
    reject = fail;
  });
  return { promise, reject };
}
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("non-React PDF imports share bounded, route-owned recovery", () => {
  let reload;
  let location;
  beforeEach(() => {
    sessionStorage.clear();
    reload = vi.fn();
    location = { pathname: "/pdf-a", search: "", hash: "", reload };
    Object.defineProperty(window, "location", {
      value: location,
      configurable: true,
      writable: true,
    });
    delete window.__RELOADING_FOR_VITE_PRELOAD_ERROR;
    window.dispatchEvent(new Event("pageshow"));
  });
  afterEach(() => {
    window.dispatchEvent(new Event("pageshow"));
    vi.restoreAllMocks();
  });

  it.each([
    ["PDF library", 0],
    ["worker URL", 1],
  ])(
    "recovers a missing current-route %s chunk once",
    async (_name, missingIndex) => {
      // Reject each dependency independently, with the other import succeeding.
      const load = createPdfjsLoader(() =>
        Promise.all(
          modules().map((module, index) =>
            index === missingIndex
              ? Promise.reject(chunkFailure())
              : Promise.resolve(module),
          ),
        ),
      );
      load().catch(() => {});
      await settle();
      expect(reload).toHaveBeenCalledTimes(1);
      expect(window.__RELOADING_FOR_VITE_PRELOAD_ERROR).toBe(true);
      reload.mockClear();
      delete window.__RELOADING_FOR_VITE_PRELOAD_ERROR;
      window.dispatchEvent(new Event("pageshow"));
      // A fresh document creates a fresh loader, but keeps sessionStorage.
      const error = chunkFailure();
      const afterReload = createPdfjsLoader(() => Promise.reject(error));
      await expect(afterReload()).rejects.toBe(error);
      expect(reload).not.toHaveBeenCalled();
      expect(window.__RELOADING_FOR_VITE_PRELOAD_ERROR).toBeUndefined();
    },
  );

  it("does not reload route B for a late failed PDF import from route A", async () => {
    const pending = pendingImport();
    const load = createPdfjsLoader(() => pending.promise);
    const error = chunkFailure();
    const rejected = expect(load()).rejects.toBe(error);
    location.pathname = "/route-b";
    pending.reject(error);
    await rejected;
    expect(reload).not.toHaveBeenCalled();
    expect(window.__RELOADING_FOR_VITE_PRELOAD_ERROR).toBeUndefined();
    // A downstream boundary must not claim the old error for B either.
    expect(requestChunkReload(error)).toBe(false);
  });

  it("does not reload during document departure before the URL commits", async () => {
    const pending = pendingImport();
    const load = createPdfjsLoader(() => pending.promise);
    const error = new TypeError("error loading dynamically imported module");
    const rejected = expect(load()).rejects.toBe(error);
    window.dispatchEvent(new Event("beforeunload", { cancelable: true }));
    pending.reject(error);
    await rejected;
    expect(reload).not.toHaveBeenCalled();
  });

  it.each([
    new DOMException("The operation was aborted", "AbortError"),
    new TypeError(
      "error loading dynamically imported module: NS_BINDING_ABORTED",
    ),
    new Error("PDF configuration is invalid"),
  ])(
    "does not recover cancellations or non-chunk errors: %s",
    async (error) => {
      const load = createPdfjsLoader(() => Promise.reject(error));
      await expect(load()).rejects.toBe(error);
      expect(reload).not.toHaveBeenCalled();
      expect(window.__RELOADING_FOR_VITE_PRELOAD_ERROR).toBeUndefined();
    },
  );

  it("clears a rejected shared promise so a later attempt can import again", async () => {
    const pending = pendingImport();
    const imported = modules();
    const importer = vi
      .fn()
      .mockReturnValueOnce(pending.promise)
      .mockResolvedValueOnce(imported);
    const load = createPdfjsLoader(importer);
    const error = chunkFailure();
    const rejected = expect(load()).rejects.toBe(error);
    location.pathname = "/route-b";
    pending.reject(error);
    await rejected;
    await expect(load()).resolves.toBe(imported[0]);
    expect(importer).toHaveBeenCalledTimes(2);
    expect(imported[0].GlobalWorkerOptions.workerSrc).toBe("/worker.js");
    expect(reload).not.toHaveBeenCalled();
  });

  it("shares successful loads without requiring a React default export", async () => {
    const imported = modules();
    const importer = vi.fn().mockResolvedValue(imported);
    const load = createPdfjsLoader(importer);
    const values = await Promise.all([load(), load(), load()]);
    expect(values).toEqual([imported[0], imported[0], imported[0]]);
    expect(importer).toHaveBeenCalledTimes(1);
    expect(reload).not.toHaveBeenCalled();
  });

  it("gives each current subscriber ownership of a shared in-flight load", async () => {
    const pending = pendingImport();
    const importer = vi.fn().mockReturnValue(pending.promise);
    const load = createPdfjsLoader(importer);
    const error = chunkFailure();
    const abandoned = expect(load()).rejects.toBe(error);
    location.pathname = "/pdf-b";
    load().catch(() => {});
    pending.reject(error);
    await abandoned;
    await settle();
    expect(importer).toHaveBeenCalledTimes(1);
    expect(reload).toHaveBeenCalledTimes(1);
    expect(
      JSON.parse(sessionStorage.getItem("chunk_reload_attempted")).scope,
    ).toContain("/pdf-b");
  });

  it("returns named-only library modules unchanged", async () => {
    const module = { doWork: () => "ok" };
    await expect(
      importWithRecovery(() => Promise.resolve(module)),
    ).resolves.toBe(module);
    expect(reload).not.toHaveBeenCalled();
  });

  it("shares the reload budget with React imports and the app boundary", async () => {
    expect(requestChunkReload()).toBe(true);
    reload.mockClear();
    delete window.__RELOADING_FOR_VITE_PRELOAD_ERROR;
    const error = chunkFailure();
    await expect(importWithRecovery(() => Promise.reject(error))).rejects.toBe(
      error,
    );
    expect(reload).not.toHaveBeenCalled();
    expect(window.__RELOADING_FOR_VITE_PRELOAD_ERROR).toBeUndefined();
  });

  it("preserves synchronous non-chunk exceptions without recovery", async () => {
    const error = new Error("bad importer");
    await expect(
      importWithRecovery(() => {
        throw error;
      }),
    ).rejects.toBe(error);
    expect(reload).not.toHaveBeenCalled();
  });

  it("surfaces a missing chunk when the reload budget cannot be persisted", async () => {
    vi.spyOn(sessionStorage, "setItem").mockImplementation(() => {
      throw new Error("storage denied");
    });
    const error = chunkFailure();
    const load = createPdfjsLoader(() => Promise.reject(error));
    await expect(load()).rejects.toBe(error);
    expect(reload).not.toHaveBeenCalled();
    expect(window.__RELOADING_FOR_VITE_PRELOAD_ERROR).toBeUndefined();
  });
});
