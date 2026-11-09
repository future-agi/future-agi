import { useEffect, useRef, useState } from "react";

// Keep this key/schema in sync with index.html's first-script intake.
const ONBOARDING_STORAGE_KEY = "fai.auth.onboarding.v1";
const ONBOARDING_KEYS = ["onboarding_token", "onboarding_gcp_token"];

// End tab-wide recovery at an account boundary or terminal signup outcome.
// Mounted readers keep their own retry copy; session handoffs are untouched.
export function clearOnboardingRecovery() {
  try {
    window.sessionStorage.removeItem(ONBOARDING_STORAGE_KEY);
  } catch {
    // Blocked storage must not prevent clearing the in-memory onboarding stash.
  }
  const source = window.__faiAuthRedirect;
  if (!source || !ONBOARDING_KEYS.some((key) => source.params.has(key))) return;
  ONBOARDING_KEYS.forEach((key) => source.params.delete(key));
  if (source.params.size === 0) delete window.__faiAuthRedirect;
}

function readStoredOnboarding() {
  try {
    return window.sessionStorage.getItem(ONBOARDING_STORAGE_KEY);
  } catch {
    return null;
  }
}

function restoreOnboarding(stored) {
  try {
    const record = JSON.parse(stored);
    if (
      typeof record?.pathname !== "string" ||
      !Array.isArray(record.params) ||
      !record.params.length ||
      !record.params.every(
        (entry) =>
          Array.isArray(entry) &&
          entry.length === 2 &&
          ONBOARDING_KEYS.includes(entry[0]) &&
          typeof entry[1] === "string",
      )
    ) {
      return null;
    }
    const source = {
      pathname: record.pathname,
      params: new URLSearchParams(record.params),
    };
    Object.defineProperty(window, "__faiAuthRedirect", {
      configurable: true,
      enumerable: false,
      value: source,
    });
    return source;
  } catch {
    return null;
  }
}

function clearStoredOnboarding(stored) {
  try {
    // A stale reader must not consume a newer redirect's recovery record.
    if (stored !== null && readStoredOnboarding() === stored) {
      window.sessionStorage.removeItem(ONBOARDING_STORAGE_KEY);
    }
  } catch {
    // Storage may be blocked. Never prevent the reader's memory cleanup.
  }
}

// index.html captures these before analytics starts. Reading must not consume
// the handoff: effects replay in StrictMode and forms may retry asynchronously.
export function readAuthRedirectParams() {
  let source = window.__faiAuthRedirect;
  let params = new URLSearchParams(window.location.search);
  const stored = readStoredOnboarding();
  // An in-memory handoff wins. Without one, an explicit onboarding URL is a
  // fresh fallback redirect, not the older registration saved in this tab.
  if (!source && !ONBOARDING_KEYS.some((key) => params.has(key))) {
    source = restoreOnboarding(stored);
  }
  const matchesSource = source?.pathname === window.location.pathname;
  if (matchesSource) {
    // The intake wins over URL values added after it ran. Preserve first-value
    // semantics for duplicate keys, matching URLSearchParams at the old URL.
    new Set(source.params.keys()).forEach((key) => params.delete(key));
    source.params.forEach((value, key) => params.append(key, value));
  }
  const release = ({ retainOnboarding = false } = {}) => {
    // A finishing old reader must not erase a newer redirect's handoff.
    if (source && window.__faiAuthRedirect === source) {
      // An unmounted form may return via an SPA link or Back. Only onboarding
      // survives abandonment; session credentials remain single-use. A reader
      // on another path cannot consume a pending registration either.
      const retained =
        retainOnboarding || !matchesSource
          ? [...source.params].filter(([key]) => ONBOARDING_KEYS.includes(key))
          : [];
      if (retained.length) {
        source.params = new URLSearchParams(retained);
        // An in-flight registration may still succeed after unmount; keep
        // its identity so that completion can consume this same handoff.
        return;
      }
      delete window.__faiAuthRedirect;
      if (!retainOnboarding) clearStoredOnboarding(stored);
    } else if (!source && !window.__faiAuthRedirect && !retainOnboarding) {
      clearStoredOnboarding(stored);
    }
    source = null;
  };
  return {
    get: (key) => params.get(key),
    // Mounted forms can release the window handoff but still need their copy
    // when returning from confirmation. Full clear also forgets that copy.
    release,
    clear: (options) => {
      release(options);
      params = new URLSearchParams();
    },
  };
}

export function useAuthRedirectParams() {
  const [params] = useState(readAuthRedirectParams);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      // StrictMode does cleanup -> setup synchronously. A real unmount clears
      // the reader and session handoff, but onboarding stays available until
      // successful registration consumes both memory and tab-scoped recovery.
      queueMicrotask(() => {
        if (!mounted.current) params.clear({ retainOnboarding: true });
      });
    };
  }, [params]);
  return params;
}
