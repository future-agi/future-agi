// @ts-nocheck
/* eslint-disable no-restricted-globals */
/* eslint-env serviceworker */
/* eslint-disable no-undef */

// Service Worker for Future AGI
//
// Strategy:
//   HTML/auth/API/non-static → network-only (no cache reads or writes)
//   Static JS/CSS/workers under /assets/ → network-first with offline fallback
//   Static fonts/images → cache-first (existing file-extension allowlist)
//
// Why NOT cache-first for JS:
//   After a deploy, chunk filenames change (e.g. TraceGrid-abc123.js → TraceGrid-def456.js).
//   If the SW serves a cached old index.html that references old chunks, or the browser
//   has a stale index.html, it requests old chunk URLs that no longer exist on the server.
//   Network-first ensures we always try the server first, falling back to cache only offline.

// Rotate again: v3 could contain authenticated /omega/ responses as well as
// credential-bearing keys in older caches. Purging requires this worker to activate.
const CACHE_NAME = "futureagi-v4";
const STATIC_DESTINATIONS = new Set([
  "script",
  "style",
  "font",
  "image",
  "worker",
]);
const IMMUTABLE_ASSET_PATH = /\.(woff2?|ttf|eot|png|jpg|jpeg|gif|svg|ico)$/;

// Install — skip waiting to activate immediately
self.addEventListener("install", () => {
  self.skipWaiting();
});

// Activate — claim clients and clean old caches
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(
          names
            .filter((name) => name !== CACHE_NAME)
            .map((name) => caches.delete(name)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

// Fetch
self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Only handle GET requests from our origin
  if (request.method !== "GET") return;
  if (url.origin !== self.location.origin) return;

  // Skip chrome extensions, hot module replacement, API calls
  if (url.protocol === "chrome-extension:") return;
  if (url.pathname.startsWith("/api/")) return;
  if (url.pathname.startsWith("/omega/")) return;
  if (url.pathname.includes("__vite")) return;

  event.respondWith(handleFetch(request, url));
});

function isNetworkOnlyUrl(url) {
  // Query strings are never safe cache keys, regardless of parameter names.
  if (url.search !== "") return true;

  let decodedPathname;
  try {
    decodedPathname = decodeURIComponent(url.pathname);
  } catch {
    // An undecodable path cannot be classified safely, so keep it network-only.
    return true;
  }

  // Reserve whole route prefixes: encoded separators must not evade the guard.
  return [url.pathname, decodedPathname].some((pathname) => {
    const path = pathname.toLowerCase();
    return [
      "/api/",
      "/omega/",
      "/shared/",
      "/review/",
      "/sos",
      "/auth/jwt/invitation/",
      "/auth/jwt/verify/",
      "/accounts/accept-invitation/",
      "/accounts/password-reset-confirm/",
    ].some((prefix) => path.startsWith(prefix));
  });
}

async function handleFetch(request, url) {
  // Never persist sensitive URLs or serve their old cached responses, even offline.
  // Check before asset matching: a credential can end in an image/font extension.
  if (
    request.mode === "navigate" ||
    request.destination === "document" ||
    request.headers.has("authorization") ||
    isNetworkOnlyUrl(url)
  ) {
    return fetch(request);
  }

  // Allow only static destinations and known asset paths. API/programmatic fetches
  // have an empty destination, including cookie-bearing credentials: "include"
  // requests. Do not infer public data from an extension or missing auth header.
  const isImmutableAsset = IMMUTABLE_ASSET_PATH.test(url.pathname);
  if (
    !STATIC_DESTINATIONS.has(request.destination) ||
    (!url.pathname.startsWith("/assets/") && !isImmutableAsset)
  ) {
    return fetch(request);
  }

  // Immutable assets (fonts, images) — cache-first
  if (isImmutableAsset) {
    return cacheFirst(request);
  }

  // Allowlisted JS/CSS/workers — network-first to prevent stale chunk errors.
  return networkFirst(request);
}

async function networkFirst(request) {
  try {
    const response = await fetch(request);
    // Cache successful responses for offline fallback
    if (response.ok) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    // Network failed — try cache (offline support)
    const cached = await caches.match(request);
    return cached || new Response("Offline", { status: 503 });
  }
}

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;

  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    return new Response("Offline", { status: 503 });
  }
}
