import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, URL } from "node:url";
import { runInNewContext } from "node:vm";
import { describe, expect, it, vi } from "vitest";

const ORIGIN = "https://app.example.test";
const CONSERVATIVE_NETWORK_ONLY_PATHS = [
  "/shared/%2Ffake-token.svg",
  "/review/%2Ffake.js",
  "/SHARED/%2ffake-token.woff2",
  "/%73hared%2F%2Ffake-token.svg",
  "/%52EVIEW%2f%2ffake.js",
  "/shared/",
  "/review/",
  "/auth/jwt/invitation/accept/%2Ffake-uuid/fake-token.svg",
  "/auth/jwt/invitation/set-password/fake-uuid/%2Ffake-token.woff2",
  "/auth/jwt/verify/%2Ffake-uuid/%2Ffake-token.js",
  "/AUTH/JWT/%49NVITATION/accept/fake-uuid/%2ffake-token.svg",
  "/%61uth%2Fjwt%2Fverify%2F%2Ffake-token.svg",
  "/auth/jwt/invitation/fake-action.js",
  "/auth/jwt/verify/fake-resource.svg",
  "/accounts/accept-invitation/fake-uuid/fake-token.svg",
  "/accounts/password-reset-confirm/fake-uuid/fake-token.js",
  "/ACCOUNTS/ACCEPT-INVITATION/%2Ffake-token.woff2",
  "/%61ccounts/%70assword-reset-confirm/fake-uuid/%2Ffake-token.svg",
  "/accounts%2Faccept-invitation%2Ffake-token.js",
  "/accounts/accept-invitation/",
  "/accounts/password-reset-confirm/",
  "/sos",
  "/SoS/",
  "/sos/fake-token.svg",
  "/%53oS%2Ffake-token.js",
  "/sos-help/fake.js",
  "/sos-fake-token.svg",
  "/%73os-fake-token.svg",
  "/SoS-fake-token.svg",
  "/%53oS-fake-token.svg",
  "/assets/app-abc123.js?v=1",
  "/assets/app-abc123.css?v=1",
  "/assets/logo-abc123.svg?v=1",
  "/assets/font-abc123.woff2?v=1",
  "/assets/app-abc123.js?fake-flag",
  "/assets/logo-abc123.svg?=fake",
  "/assets/malformed-%E0%A4%A.svg",
];
// Use a filesystem path so Vite's web transform cannot rewrite a public URL.
const workerSource = readFileSync(
  join(
    dirname(fileURLToPath(import.meta.url)),
    "..",
    "..",
    "..",
    "public",
    "service-worker.js",
  ),
  "utf8",
);

function createWorker() {
  const listeners = new Map();
  const clonedResponse = { body: "fake network response clone" };
  const response = {
    ok: true,
    clone: vi.fn(() => clonedResponse),
  };
  const cache = { put: vi.fn().mockResolvedValue(undefined) };
  const caches = {
    open: vi.fn().mockResolvedValue(cache),
    match: vi.fn().mockResolvedValue(undefined),
    keys: vi
      .fn()
      .mockResolvedValue([
        "futureagi-v1",
        "futureagi-v2",
        "futureagi-v3",
        "futureagi-v4",
        "unrelated-cache",
      ]),
    delete: vi.fn().mockResolvedValue(true),
  };
  const fetch = vi.fn().mockResolvedValue(response);
  const self = {
    location: { origin: ORIGIN },
    addEventListener: (type, listener) => listeners.set(type, listener),
    skipWaiting: vi.fn(),
    clients: { claim: vi.fn().mockResolvedValue(undefined) },
  };

  // No browser or network: the public script sees only these fake worker APIs.
  runInNewContext(workerSource, { self, caches, fetch, URL });

  return {
    cache,
    caches,
    clonedResponse,
    fetch,
    listeners,
    response,
    self,
    dispatch(path, overrides = {}) {
      const request = {
        url: new URL(path, ORIGIN).href,
        method: "GET",
        mode: "cors",
        // Exercise URL guards with a cacheable destination, not an API default.
        destination: "script",
        credentials: "same-origin",
        headers: new Headers(),
        ...overrides,
      };
      const event = { request, respondWith: vi.fn() };
      listeners.get("fetch")(event);
      return event;
    },
  };
}

async function fetchRequest(worker, path, overrides) {
  const event = worker.dispatch(path, overrides);
  expect(event.respondWith).toHaveBeenCalledOnce();
  const response = await event.respondWith.mock.calls[0][0];
  return { request: event.request, response };
}

function expectNetworkOnly(worker, result) {
  expect(worker.fetch).toHaveBeenCalledWith(result.request);
  expect(result.response).toBe(worker.response);
  expect(worker.caches.open).not.toHaveBeenCalled();
  expect(worker.caches.match).not.toHaveBeenCalled();
  expect(worker.cache.put).not.toHaveBeenCalled();
  expect(worker.response.clone).not.toHaveBeenCalled();
}

describe("service worker credential cache isolation", () => {
  it.each([
    {
      name: "navigation with marketplace credentials",
      path: "/dashboard/develop?access_token=fake&refresh_token=fake",
      overrides: { mode: "navigate", destination: "document" },
    },
    {
      name: "navigation without credentials",
      path: "/dashboard/develop",
      overrides: { mode: "navigate" },
    },
    {
      name: "document destination even with an asset extension",
      path: "/assets/shell-abc123.js",
      overrides: { destination: "document" },
    },
  ])("fetches $name without reading or writing caches", async (scenario) => {
    const worker = createWorker();
    worker.caches.match.mockResolvedValue({ body: "fake stale page" });
    const result = await fetchRequest(
      worker,
      scenario.path,
      scenario.overrides,
    );
    expectNetworkOnly(worker, result);
  });

  describe.each(["miss", "hit"])("credential cache %s", (cacheState) => {
    it.each([
      ...CONSERVATIVE_NETWORK_ONLY_PATHS,
      "/dashboard/develop?sso_token=fake",
      "/dashboard/develop?access_token=fake&refresh_token=fake",
      "/assets/app-abc123.js?refresh_token=fake",
      "/assets/logo-abc123.svg?onboarding_token=fake",
      "/assets/font-abc123.woff2?onboarding_gcp_token=fake",
      "/assets/app-abc123.css?access_token=",
      "/assets/app-abc123.js?%73so_token=fake",
      ...[
        "SSO_TOKEN",
        "Sso_Token",
        "%53so%5FToken",
        "ACCESS_TOKEN",
        "Access_Token",
        "%41ccess_token",
        "REFRESH_TOKEN",
        "Refresh_Token",
        "%52efresh_Token",
        "ONBOARDING_TOKEN",
        "Onboarding_Token",
        "%4Fnboarding_Token",
        "ONBOARDING_GCP_TOKEN",
        "Onboarding_Gcp_Token",
        "%4Fnboarding_Gcp_Token",
      ].flatMap((name) => [
        `/assets/app-abc123.js?${name}=fake`,
        `/assets/logo-abc123.svg?${name}=fake`,
        `/assets/font-abc123.woff2?${name}=fake`,
      ]),
      "/sos?access=fake&refresh=fake",
      "/SOS?access=fake",
      "/SoS/?refresh=fake",
      "/sos?ACCESS=fake",
      "/sos?Access=fake",
      "/sos?REFRESH=fake",
      "/sos?Refresh=fake",
      "/sos?%41ccess=fake",
      "/sos?%52efresh=fake",
      "/%73os?access=fake",
      "/%73os?refresh=fake",
      "/%53oS/?%52efresh=fake",
      "/auth/jwt/invitation/accept/fake-uuid/fake-token",
      "/auth/jwt/invitation/set-password/fake-uuid/fake-token",
      "/auth/jwt/verify/fake-uuid/fake-token",
      "/shared/fake-token",
      "/shared/fake-token.svg",
      "/review/fake-token",
      "/review/fake-token.js/",
      "/auth/jwt/%69nvitation/accept/fake-uuid/fake-token.svg",
      "/auth/jwt/invitation/%61ccept/fake-uuid/fake-token",
      "/auth/jwt/invitation/%73et-password/fake-uuid/fake-token.woff2",
      "/%61uth/%6Awt/invitation/accept/fake-uuid/fake-token",
      "/auth/jwt/%76erify/fake-uuid/fake-token.svg",
      "/AUTH/JWT/%56ERIFY/fake-uuid/fake-token.woff2",
      "/%73hared/fake-token.svg",
      "/%53HARED/fake-token.woff2",
      "/%72eview/fake-token",
      "/%52EVIEW/fake-token.woff2",
      "/assets/malformed-%.svg",
      "/assets/malformed-%2.js",
      "/assets/malformed-%GG.woff2",
      "/assets/malformed-%C0%AF.svg",
    ])("never reads or stores a network-only URL %s", async (path) => {
      const worker = createWorker();
      if (cacheState === "hit") {
        worker.caches.match.mockResolvedValue({
          body: "fake stale credential page",
        });
      }
      expectNetworkOnly(worker, await fetchRequest(worker, path));
    });
  });

  it.each([
    ...CONSERVATIVE_NETWORK_ONLY_PATHS.map((path) => ({ path })),
    { path: "/dashboard", overrides: { mode: "navigate" } },
    { path: "/assets/app-abc123.js?access_token=fake" },
    { path: "/sos?refresh=fake" },
    { path: "/shared/fake-token.svg" },
    { path: "/assets/app-abc123.js?SSO_TOKEN=fake" },
    { path: "/assets/logo-abc123.svg?%41ccess_token=fake" },
    { path: "/sos?Refresh=fake" },
    { path: "/%73os?%52efresh=fake" },
    { path: "/auth/jwt/%76erify/fake-uuid/fake-token.svg" },
    { path: "/%73hared/fake-token.svg" },
    { path: "/%72eview/fake-token.woff2" },
    { path: "/assets/malformed-%.svg" },
  ])("does not fall back to cached credentials offline: $path", async (row) => {
    const worker = createWorker();
    const failure = new Error("fake offline network");
    worker.fetch.mockRejectedValue(failure);
    worker.caches.match.mockResolvedValue({
      body: "fake stale credential page",
    });

    await expect(fetchRequest(worker, row.path, row.overrides)).rejects.toBe(
      failure,
    );
    expect(worker.caches.match).not.toHaveBeenCalled();
    expect(worker.caches.open).not.toHaveBeenCalled();
    expect(worker.cache.put).not.toHaveBeenCalled();
  });
});

// An unhandled fetch event uses the browser's network path, never Cache Storage.
async function fetchWithBrowserFallback(worker, path, overrides) {
  const event = worker.dispatch(path, overrides);
  const response = await (event.respondWith.mock.calls[0]?.[0] ??
    worker.fetch(event.request));
  return { request: event.request, response };
}

describe.each([
  {
    path: "/omega/api/fake",
    overrides: { destination: "", credentials: "include" },
  },
  {
    path: "/omega/api/fake.svg",
    overrides: { destination: "image", credentials: "include" },
  },
  { path: "/omega/assets/fake.js" },
  { path: "/api/fake", overrides: { destination: "", credentials: "include" } },
  {
    path: "/%6fmega%2fapi/fake.svg",
    overrides: { destination: "image", credentials: "include" },
  },
  {
    path: "/assets/app-abc123.js",
    overrides: { headers: new Headers({ Authorization: "Bearer fake" }) },
  },
  {
    path: "/assets/font-abc123.woff2",
    overrides: {
      destination: "font",
      headers: new Headers({ authorization: "Bearer fake" }),
    },
  },
  {
    path: "/assets/logo-abc123.svg",
    overrides: {
      destination: "image",
      headers: new Headers({ AUTHORIZATION: "" }),
    },
  },
  {
    path: "/workspace/fake",
    overrides: { destination: "", credentials: "include" },
  },
  {
    path: "/workspace/fake.svg",
    overrides: { destination: "", credentials: "include" },
  },
  {
    path: "/assets/app-abc123.js",
    overrides: { destination: "", credentials: "include" },
  },
  { path: "/assets/app-abc123.js", overrides: { destination: "" } },
  { path: "/assets/frame-abc123.js", overrides: { destination: "iframe" } },
  { path: "/dashboard", overrides: { destination: "" } },
  // These unversioned scripts are outside the static asset allowlist.
  ...[
    "/shared-assets/fake.js",
    "/review-assets/fake.js",
    "/auth/jwt/verification/fake.js",
    "/accounts/accept-invitation-assets/fake.js",
    "/accounts/password-reset-confirm-assets/fake.js",
  ].map((path) => ({ path })),
])(
  "service worker authenticated API and non-static isolation: $path",
  (row) => {
    it("never reads or stores the request online", async () => {
      const worker = createWorker();
      worker.caches.match.mockResolvedValue({
        body: "fake previous tenant data",
      });
      expectNetworkOnly(
        worker,
        await fetchWithBrowserFallback(worker, row.path, row.overrides),
      );
    });

    it("never serves previous tenant data offline", async () => {
      const worker = createWorker();
      const failure = new Error("fake offline network");
      worker.fetch.mockRejectedValue(failure);
      worker.caches.match.mockResolvedValue({
        body: "fake previous tenant data",
      });
      await expect(
        fetchWithBrowserFallback(worker, row.path, row.overrides),
      ).rejects.toBe(failure);
      expect(worker.caches.match).not.toHaveBeenCalled();
      expect(worker.caches.open).not.toHaveBeenCalled();
      expect(worker.cache.put).not.toHaveBeenCalled();
    });
  },
);

describe("service worker asset caching", () => {
  it.each([
    { path: "/assets/app-abc123.js", destination: "script" },
    { path: "/assets/app-abc123.css", destination: "style" },
    { path: "/assets/worker-abc123.js", destination: "worker" },
  ])("keeps network-first storage for $path", async ({ path, destination }) => {
    const worker = createWorker();
    worker.caches.match.mockResolvedValue({ body: "fake old asset" });
    const result = await fetchRequest(worker, path, {
      destination,
      credentials: "include",
    });

    expect(worker.fetch).toHaveBeenCalledWith(result.request);
    expect(result.response).toBe(worker.response);
    expect(worker.caches.match).not.toHaveBeenCalled();
    expect(worker.caches.open).toHaveBeenCalledWith("futureagi-v4");
    expect(worker.cache.put).toHaveBeenCalledWith(
      result.request,
      worker.clonedResponse,
    );
  });

  it("keeps the offline fallback for a cached hashed JavaScript asset", async () => {
    const worker = createWorker();
    const cached = { body: "fake cached asset" };
    worker.fetch.mockRejectedValue(new Error("fake offline network"));
    worker.caches.match.mockResolvedValue(cached);
    const result = await fetchRequest(worker, "/assets/app-abc123.js");

    expect(result.response).toBe(cached);
    expect(worker.caches.match).toHaveBeenCalledWith(result.request);
    expect(worker.cache.put).not.toHaveBeenCalled();
  });

  it.each([
    { path: "/assets/font-abc123.woff2", destination: "font" },
    { path: "/fonts/font-abc123.woff2", destination: "font" },
    { path: "/assets/logo-abc123.svg", destination: "image" },
    { path: "/images/logo.svg", destination: "image" },
  ])(
    "keeps cache-first hits offline for $path",
    async ({ path, destination }) => {
      const worker = createWorker();
      const cached = { body: "fake cached immutable asset" };
      worker.fetch.mockRejectedValue(new Error("fake offline network"));
      worker.caches.match.mockResolvedValue(cached);
      const result = await fetchRequest(worker, path, {
        destination,
        credentials: "include",
      });

      expect(result.response).toBe(cached);
      expect(worker.caches.match).toHaveBeenCalledWith(result.request);
      expect(worker.fetch).not.toHaveBeenCalled();
      expect(worker.cache.put).not.toHaveBeenCalled();
    },
  );

  it.each([
    { path: "/assets/font-abc123.woff2", destination: "font" },
    { path: "/fonts/font-abc123.woff2", destination: "font" },
    { path: "/assets/logo-abc123.svg", destination: "image" },
    { path: "/images/logo.svg", destination: "image" },
  ])("stores cache-first misses for $path", async ({ path, destination }) => {
    const worker = createWorker();
    const result = await fetchRequest(worker, path, {
      destination,
      credentials: "include",
    });

    expect(worker.caches.match).toHaveBeenCalledWith(result.request);
    expect(worker.fetch).toHaveBeenCalledWith(result.request);
    expect(worker.caches.open).toHaveBeenCalledWith("futureagi-v4");
    expect(worker.cache.put).toHaveBeenCalledWith(
      result.request,
      worker.clonedResponse,
    );
  });
});

describe("service worker activation", () => {
  it("deletes every other cache including tenant-bearing v3 before claiming clients", async () => {
    const worker = createWorker();
    const event = { waitUntil: vi.fn() };
    worker.listeners.get("activate")(event);
    expect(event.waitUntil).toHaveBeenCalledOnce();
    await event.waitUntil.mock.calls[0][0];

    expect(worker.caches.delete.mock.calls).toEqual([
      ["futureagi-v1"],
      ["futureagi-v2"],
      ["futureagi-v3"],
      ["unrelated-cache"],
    ]);
    expect(worker.caches.delete).not.toHaveBeenCalledWith("futureagi-v4");
    expect(worker.self.clients.claim).toHaveBeenCalledOnce();
    expect(worker.caches.delete.mock.invocationCallOrder[3]).toBeLessThan(
      worker.self.clients.claim.mock.invocationCallOrder[0],
    );
  });
});
