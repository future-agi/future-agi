import { lazy } from "react";
import {
  CHUNK_IMPORT_MAX_RETRIES,
  CHUNK_IMPORT_RETRY_BASE_DELAY_MS,
  CHUNK_IMPORT_TIMEOUT_MS,
} from "src/config/runtime_limits";

import {
  canRecoverChunk,
  getChunkReloadScope,
  isChunkError,
  rememberChunkError,
  requestChunkReload,
} from "./chunkRecovery";

export {
  clearChunkReloadAttempt,
  isChunkError,
  requestChunkReload,
} from "./chunkRecovery";

function persistentChunkError(message) {
  const error = new Error(message);
  error.name = "ChunkLoadError";
  error.skipChunkRetry = true;
  return error;
}

function importWithTimeout(importPromise) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      reject(
        persistentChunkError(
          `Failed to fetch dynamically imported module (timed out after ${CHUNK_IMPORT_TIMEOUT_MS}ms)`,
        ),
      );
    }, CHUNK_IMPORT_TIMEOUT_MS);

    Promise.resolve(importPromise).then(
      (module) => {
        clearTimeout(timeout);
        resolve(module);
      },
      (error) => {
        clearTimeout(timeout);
        reject(error);
      },
    );
  });
}

/**
 * Drop-in replacement for React.lazy that retries failed dynamic imports.
 *
 * After a deployment, old chunks may no longer exist on the server.
 * This wrapper:
 *   1. Retries the import up to `maxRetries` times while its route is current
 *   2. On final failure, does a silent one-time page reload (uses sessionStorage
 *      to prevent infinite loops)
 *   3. No version banners or update notifications — completely invisible to users
 *
 * Usage:
 *   const MyPage = lazyWithRetry(() => import("./MyPage"));
 */
export default function lazyWithRetry(
  importFn,
  maxRetries = CHUNK_IMPORT_MAX_RETRIES,
) {
  return lazy(() => retryImport(importFn, maxRetries));
}

// Exported for unit testing the post-deploy recovery logic directly.
export async function retryImport(
  importFn,
  retriesLeft,
  attempt = 1,
  scope = getChunkReloadScope(),
) {
  try {
    const module = await importWithTimeout(importFn());

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
      // Use the same route-aware recovery below as rejected imports.
      throw persistentChunkError(
        "Failed to fetch dynamically imported module (resolved without a default export)",
      );
    }

    // Do not clear the reload guard here. An earlier lazy import can succeed
    // while a nested route chunk still fails; clearing globally at that point
    // was the cause of the DEV reload loop. The entrypoint + URL scope changes
    // naturally for a new deployment or route, and explicit Retry clears it.
    return module;
  } catch (error) {
    // Capture ownership once, not when a retry finally fails on a newer route.
    // The boundary receives the same error and must honor that ownership too.
    rememberChunkError(error, scope);
    if (!canRecoverChunk(scope, error)) throw error;

    if (retriesLeft > 0 && isChunkError(error) && !error?.skipChunkRetry) {
      // Wait briefly — CDN may need time to propagate new chunks
      await new Promise((resolve) =>
        setTimeout(resolve, CHUNK_IMPORT_RETRY_BASE_DELAY_MS * attempt),
      );
      if (!canRecoverChunk(scope, error)) throw error;
      return retryImport(importFn, retriesLeft - 1, attempt + 1, scope);
    }

    // All retries exhausted — try a silent one-time page reload
    if (isChunkError(error) && requestChunkReload(error)) {
      // Return a never-resolving promise to prevent React error boundary
      // while the page reloads
      return new Promise(() => {});
    }

    // Not a chunk error or reload already attempted — throw original error
    throw error;
  }
}
