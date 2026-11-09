// Loaded before main.jsx, and shared by React and non-React import owners.
// A preloadError has no requesting route identity. Do not prevent its default:
// Vite must reject the import so its owner can decide whether to recover.
const RELOAD_KEY = "chunk_reload_attempted";
const errorScopes = new WeakMap();
let documentLeaving = false;

window.addEventListener("beforeunload", (event) => {
  // During a document navigation the old URL remains visible until commit.
  // In particular Firefox may reject its imports before that URL changes.
  documentLeaving = true;
  queueMicrotask(() => {
    if (event.defaultPrevented) documentLeaving = false;
  });
});
window.addEventListener("pagehide", () => {
  documentLeaving = true;
});
window.addEventListener("pageshow", () => {
  documentLeaving = false;
});

export function getChunkReloadScope() {
  const scripts = document.querySelectorAll('script[type="module"][src]');
  // The last module script is the app entry, not the early recovery module.
  const entrypoint = scripts[scripts.length - 1]?.src || "app";
  const { pathname = "", search = "", hash = "" } = window.location;
  return `${entrypoint}|${pathname}${search}${hash}`;
}

export function rememberChunkError(error, scope) {
  if (error && (typeof error === "object" || typeof error === "function")) {
    errorScopes.set(error, scope);
  }
}

export function canRecoverChunk(scope, error) {
  const cancelled =
    error?.name === "AbortError" ||
    /NS_BINDING_ABORTED|\b(aborted|cancelled|canceled)\b/i.test(
      error?.message || "",
    );
  return !cancelled && !documentLeaving && scope === getChunkReloadScope();
}

export function isChunkError(error) {
  if (!error) return false;
  const msg = error?.message || "";
  return (
    msg.includes("Failed to fetch dynamically imported module") ||
    msg.includes("error loading dynamically imported module") ||
    msg.includes("Importing a module script failed") ||
    msg.includes("Loading chunk") ||
    msg.includes("Loading CSS chunk") ||
    msg.includes("Unable to preload CSS") ||
    msg.includes("is not a valid JavaScript MIME type") ||
    error?.name === "ChunkLoadError"
  );
}

// Unlike React.lazy, library imports may have only named exports. Capture the
// requesting route before invoking the importer, including for cached promises:
// each subscriber owns its recovery, not just the first cache population.
export async function importWithRecovery(importFn) {
  const scope = getChunkReloadScope();
  try {
    return await importFn();
  } catch (error) {
    rememberChunkError(error, scope);
    if (isChunkError(error) && requestChunkReload(error)) {
      // Do not run callers' error/UI side effects while the document reloads.
      return new Promise(() => {});
    }
    throw error;
  }
}

export function clearChunkReloadAttempt() {
  try {
    sessionStorage.removeItem(RELOAD_KEY);
  } catch {
    // Explicit Retry still works when storage is unavailable.
  }
}

export function requestChunkReload(error) {
  const scope = errorScopes.get(error) || getChunkReloadScope();
  if (!canRecoverChunk(scope, error)) return false;

  try {
    const raw = sessionStorage.getItem(RELOAD_KEY);
    // Older builds used "1", then a single entrypoint + route record. Preserve
    // those guards and retain all scopes so A -> B -> A cannot restart a loop.
    if (raw === "1") {
      sessionStorage.setItem(
        RELOAD_KEY,
        JSON.stringify({ scope, attemptedAt: Date.now() }),
      );
      return false;
    }
    const previous = raw ? JSON.parse(raw) : null;
    const scopes = new Set(
      [
        ...(Array.isArray(previous?.scopes) ? previous.scopes : []),
        previous?.scope,
      ].filter((value) => typeof value === "string"),
    );
    if (scopes.has(scope)) return false;
    scopes.add(scope);
    sessionStorage.setItem(
      RELOAD_KEY,
      JSON.stringify({
        scope,
        scopes: [...scopes],
        attemptedAt: Date.now(),
      }),
    );
  } catch {
    // A reload cannot be bounded without a readable, durable session guard.
    // Let the original rejection reach the visible error boundary instead.
    return false;
  }

  // Preserve ReplayDrawer/useBeforeUnload's intentional-recovery exception,
  // but only after this route has actually reserved its one reload.
  window.__RELOADING_FOR_VITE_PRELOAD_ERROR = true;
  window.location.reload();
  return true;
}
