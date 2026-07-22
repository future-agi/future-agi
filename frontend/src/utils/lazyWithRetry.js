import { lazy } from "react";

const RELOAD_KEY = "chunk_reload_attempted";

/**
 * Drop-in replacement for React.lazy that retries failed dynamic imports.
 *
 * After a deployment, old chunks may no longer exist on the server.
 * This wrapper:
 *   1. Retries the import up to `maxRetries` times with cache-busting query params
 *   2. On final failure, does a silent one-time page reload (uses sessionStorage
 *      to prevent infinite loops)
 *   3. No version banners or update notifications — completely invisible to users
 *
 * Usage:
 *   const MyPage = lazyWithRetry(() => import("./MyPage"));
 */
export default function lazyWithRetry(importFn, maxRetries = 3) {
  return lazy(() => retryImport(importFn, maxRetries));
}

// Exported for unit testing the post-deploy recovery logic directly.
export async function retryImport(importFn, retriesLeft) {
  try {
    const module = await importFn();

    // A dynamic import can RESOLVE (not reject) to a module that is undefined
    // or missing its `default` export. This happens with stale module graphs
    // after a deploy: the browser's module map / SPA index.html fallback can
    // hand back a default-less namespace instead of throwing. React.lazy then
    // reads `.default` on it and throws "Cannot read properties of undefined
    // (reading 'default')" deep in the reconciler — outside this try/catch and
    // outside isChunkError(). Treat it like a chunk error and recover.
    //
    // Re-calling importFn() would just return the same cached bad module, so
    // skip the retry loop and go straight to a one-time silent reload (a fresh
    // document gets a fresh module map and a fresh index.html).
    if (!module || typeof module.default === "undefined") {
      if (!sessionStorage.getItem(RELOAD_KEY)) {
        sessionStorage.setItem(RELOAD_KEY, "1");
        window.location.reload();
        // Never-resolving promise so React keeps the Suspense fallback while
        // the page reloads, instead of surfacing the bad module to lazy().
        return new Promise(() => {});
      }
      // Reload already attempted this session — surface a recognized chunk
      // error (matched by isChunkError/ignoreErrors) rather than letting React
      // throw the opaque "reading 'default'" TypeError, and avoid a reload loop.
      throw new Error(
        "Failed to fetch dynamically imported module (resolved without a default export)",
      );
    }

    // Success — clear any previous reload flag
    sessionStorage.removeItem(RELOAD_KEY);
    return module;
  } catch (error) {
    if (retriesLeft > 0 && isChunkError(error)) {
      // Wait briefly — CDN may need time to propagate new chunks
      await new Promise((r) => setTimeout(r, 1000 * (4 - retriesLeft)));
      return retryImport(importFn, retriesLeft - 1);
    }

    // All retries exhausted — try a silent one-time page reload
    if (isChunkError(error) && !sessionStorage.getItem(RELOAD_KEY)) {
      sessionStorage.setItem(RELOAD_KEY, "1");
      window.location.reload();
      // Return a never-resolving promise to prevent React error boundary
      // while the page reloads
      return new Promise(() => {});
    }

    // Not a chunk error or reload already attempted — throw original error
    throw error;
  }
}

export function isChunkError(error) {
  if (!error) return false;
  const msg = error?.message || "";
  return (
    msg.includes("Failed to fetch dynamically imported module") ||
    msg.includes("Loading chunk") ||
    msg.includes("Loading CSS chunk") ||
    msg.includes("Unable to preload CSS") ||
    msg.includes("is not a valid JavaScript MIME type") ||
    error?.name === "ChunkLoadError"
  );
}
