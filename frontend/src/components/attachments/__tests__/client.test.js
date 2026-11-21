import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import {
  listAttachments,
  readRecordingPreview,
  resolveAttachments,
} from "../client";
import {
  MAX_EXCHANGES_PER_MINUTE,
  captureMediaContext,
  resetMediaSessions,
} from "../mediaSession";
import {
  authDefaults,
  exchangeResponse,
  gatewayPath,
  grant,
  mediaSessionId,
  occurrence,
  occurrenceId,
  standaloneManifest,
  mixedManifest,
  parent,
  projectId,
} from "./fixtures";
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn(), defaults: {} },
}));
const items = [
  { occurrence_id: occurrenceId, parent, representation: "playable" },
];
const sessionB = "e0000000-0000-0000-0000-0000000000b2";
const sessionC = "e0000000-0000-0000-0000-0000000000c3";
const orgB = "50000000-0000-0000-0000-0000000000b5";
const HANDLE = "X-Attachment-Media-Session";
const isExchange = (path) => path.endsWith("attachment-media-sessions");
const signIn = (token, organization) =>
  Object.assign(axios.defaults, authDefaults(token, organization));
beforeEach(() => {
  vi.resetAllMocks();
  signIn();
  resetMediaSessions();
});
afterEach(() => vi.useRealTimers());

// Server double: each exchange mints the next handle; resolve grants paths on
// whichever handle the request names (the backend's exact-row selection).
function serve({ handles = [mediaSessionId], lifetimeMs, results } = {}) {
  let minted = 0;
  axios.post.mockImplementation(async (path, body, config) => {
    if (isExchange(path))
      return exchangeResponse(
        handles[Math.min(minted++, handles.length - 1)],
        lifetimeMs,
      );
    const handle = config.headers[HANDLE];
    return {
      data: {
        items:
          results?.(body.items, handle) ??
          body.items.map(({ occurrence_id }) => ({
            ...grant,
            occurrence_id,
            gateway_path: gatewayPath(occurrence_id, "g", handle),
          })),
      },
    };
  });
}
const calls = () =>
  axios.post.mock.calls.map(([path, , config]) => ({
    exchange: isExchange(path),
    bearer: config.headers.Authorization,
    organization: config.headers["X-Organization-Id"],
    handle: config.headers[HANDLE],
  }));
const resolves = () => calls().filter((call) => !call.exchange);
function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("authorized attachment resolver", () => {
  it("forwards exactly its own exchange's handle and never requests source bytes", async () => {
    serve();
    const signal = new AbortController().signal;
    const [result] = await resolveAttachments(projectId, items, signal);
    expect(result.gateway_path).toBe(
      gatewayPath(occurrenceId, "g", mediaSessionId),
    );
    const [[exchangePath, exchangeBody, exchangeConfig], [path, body, config]] =
      axios.post.mock.calls;
    expect(exchangePath).toBe("/tracer/v1/attachment-media-sessions");
    expect(exchangeBody).toEqual({});
    expect(path).toBe(`/tracer/v1/projects/${projectId}/attachments/resolve`);
    expect(body).toEqual({ items });
    // Both requests run under the same captured bearer and organization,
    // same-origin with credentials; the browser supplies Origin itself.
    for (const sent of [exchangeConfig, config]) {
      expect(sent).toMatchObject({
        baseURL: window.location.origin,
        withCredentials: true,
        headers: {
          Authorization: "Bearer synthetic-access-a",
          "X-Organization-Id":
            authDefaults().headers.common["X-Organization-Id"],
        },
      });
      expect(Object.keys(sent.headers)).not.toContain("Origin");
    }
    expect(exchangeConfig.headers[HANDLE]).toBeUndefined();
    expect(config.headers[HANDLE]).toBe(mediaSessionId);
    expect(config.signal).toBe(signal);
    expect(axios.get).not.toHaveBeenCalled();
  });
  it.each([401, 403, 404, 500])(
    "does not disclose existence or raw server exceptions for %i",
    async (statusCode) => {
      axios.post.mockRejectedValue({
        statusCode,
        message: "canary signed source URL and filename",
      });
      await expect(resolveAttachments(projectId, items)).rejects.toThrow(
        "Attachment unavailable",
      );
      expect(axios.post).toHaveBeenCalledTimes(1);
    },
  );
  it("rejects a mismatched, extra or stale grant", async () => {
    for (const results of [
      [{ ...grant, occurrence_id: projectId }],
      [grant, grant],
      [{ ...grant, expires_at: "2000-01-01T00:00:00Z" }],
    ]) {
      serve({ results: () => results });
      await expect(resolveAttachments(projectId, items)).rejects.toThrow(
        "Attachment unavailable",
      );
    }
  });
  it("validates IDs and parent before any network call", async () => {
    await expect(resolveAttachments("../foreign", items)).rejects.toThrow();
    await expect(
      resolveAttachments(projectId, [
        { ...items[0], parent: { ...parent, span_id: "0" } },
      ]),
    ).rejects.toThrow();
    expect(axios.post).not.toHaveBeenCalled();
  });
  it("never reuses a grant: each call resolves again, on a reused handle", async () => {
    serve();
    await resolveAttachments(projectId, items);
    serve({
      results: () => [{ occurrence_id: occurrenceId, status: "not_found" }],
    });
    expect((await resolveAttachments(projectId, items))[0].status).toBe(
      "not_found",
    );
    // One exchange, two resolves on its handle; the grant was never cached.
    expect(calls().map((call) => call.exchange)).toEqual([true, false, false]);
    expect(resolves().map((call) => call.handle)).toEqual([
      mediaSessionId,
      mediaSessionId,
    ]);
  });
  it("sends nothing without an application bearer (signed out)", async () => {
    serve();
    axios.defaults.headers.common = {};
    await expect(resolveAttachments(projectId, items)).rejects.toThrow(
      "Attachment unavailable",
    );
    expect(axios.post).not.toHaveBeenCalled();
  });
  it("lists one bounded page without resolving or preloading bytes", async () => {
    axios.get.mockResolvedValue({
      data: { items: [occurrence], next_cursor: "next" },
    });
    expect(await listAttachments(projectId, parent, null)).toEqual({
      items: [occurrence],
      next_cursor: "next",
    });
    expect(axios.get).toHaveBeenCalledWith(
      `/tracer/v1/projects/${projectId}/attachments/occurrences`,
      expect.objectContaining({ params: { ...parent, limit: 25 } }),
    );
    expect(axios.post).not.toHaveBeenCalled();
  });
  it("denies wrong-parent/project pages and source metadata", async () => {
    for (const item of [
      { ...occurrence, parent: { ...parent, span_id: "ffffffffffffffff" } },
      {
        ...occurrence,
        scope: { ...occurrence.scope, project_id: occurrenceId },
      },
      { ...occurrence, filename: "canary-secret.wav" },
    ]) {
      axios.get.mockResolvedValue({
        data: { items: [item], next_cursor: null },
      });
      await expect(listAttachments(projectId, parent, null)).rejects.toThrow(
        "Attachment unavailable",
      );
    }
  });
});

describe("media-session handle from the exchange (F01 #675)", () => {
  const exchange = exchangeResponse();
  const expiry = exchange.headers["x-attachment-media-expires-at"];
  it.each([
    ["no handle headers (legacy exchange)", { ...exchange, headers: {} }],
    [
      "a missing expiry",
      {
        ...exchange,
        headers: { "x-attachment-media-session": mediaSessionId },
      },
    ],
    [
      "a missing handle",
      { ...exchange, headers: { "x-attachment-media-expires-at": expiry } },
    ],
    [
      "an uppercase handle",
      {
        ...exchange,
        headers: {
          ...exchange.headers,
          "x-attachment-media-session": mediaSessionId.toUpperCase(),
        },
      },
    ],
    [
      "the nil handle",
      {
        ...exchange,
        headers: {
          ...exchange.headers,
          "x-attachment-media-session": "00000000-0000-0000-0000-000000000000",
        },
      },
    ],
    [
      "a duplicated handle header",
      {
        ...exchange,
        headers: {
          ...exchange.headers,
          "x-attachment-media-session": `${mediaSessionId}, ${sessionB}`,
        },
      },
    ],
    [
      "an offset-less expiry",
      {
        ...exchange,
        headers: {
          ...exchange.headers,
          "x-attachment-media-expires-at": "2099-01-01T00:00:00",
        },
      },
    ],
    ["a near-expired handle", exchangeResponse(mediaSessionId, 10000)],
    ["a 200 instead of the bodiless 204", { ...exchange, status: 200 }],
  ])("fails closed on %s and never resolves", async (_, response) => {
    axios.post.mockResolvedValue(response);
    await expect(resolveAttachments(projectId, items)).rejects.toThrow(
      "Attachment unavailable",
    );
    expect(calls().map((call) => call.exchange)).toEqual([true]);
  });
  it("treats a granted path on another session as foreign and drops the handle", async () => {
    serve({
      handles: [mediaSessionId, sessionB],
      results: (requested) =>
        requested.map(({ occurrence_id }) => ({
          ...grant,
          occurrence_id,
          gateway_path: gatewayPath(occurrence_id, "g", sessionC),
        })),
    });
    await expect(resolveAttachments(projectId, items)).rejects.toThrow(
      "Attachment unavailable",
    );
    serve({ handles: [sessionB] });
    const [result] = await resolveAttachments(projectId, items);
    // The record that met a foreign answer is never reused.
    expect(result.gateway_path).toContain(`/${sessionB}/`);
    expect(calls().map((call) => call.exchange)).toEqual([
      true,
      false,
      true,
      false,
    ]);
  });
  it("drops a handle whose resolve failed, so the next call exchanges fresh", async () => {
    let minted = 0;
    axios.post.mockImplementation(async (path, body, config) => {
      if (isExchange(path))
        return exchangeResponse([mediaSessionId, sessionB][minted++]);
      if (config.headers[HANDLE] === mediaSessionId) throw { statusCode: 404 };
      return {
        data: {
          items: [
            {
              ...grant,
              gateway_path: gatewayPath(occurrenceId, "g", sessionB),
            },
          ],
        },
      };
    });
    await expect(resolveAttachments(projectId, items)).rejects.toThrow();
    const [result] = await resolveAttachments(projectId, items);
    expect(result.gateway_path).toContain(`/${sessionB}/`);
    expect(resolves().map((call) => call.handle)).toEqual([
      mediaSessionId,
      sessionB,
    ]);
  });
  it("asks for a new session when told the previous one was refused", async () => {
    serve({ handles: [mediaSessionId, sessionB] });
    await resolveAttachments(projectId, items);
    const [result] = await resolveAttachments(projectId, items, undefined, {
      fresh: true,
    });
    expect(result.gateway_path).toContain(`/${sessionB}/`);
    expect(resolves().map((call) => call.handle)).toEqual([
      mediaSessionId,
      sessionB,
    ]);
  });
});

describe("bounded in-memory handle reuse", () => {
  it("reuses a handle only while it can back a full grant, never extending it", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2030-01-01T00:00:00Z"));
    // The server claims a day; reuse is still capped at the 300 s lifetime.
    serve({ handles: [mediaSessionId, sessionB], lifetimeMs: 86400000 });
    await resolveAttachments(projectId, items);
    vi.setSystemTime(new Date("2030-01-01T00:03:30Z")); // 90 s left of 300
    await resolveAttachments(projectId, items);
    vi.setSystemTime(new Date("2030-01-01T00:03:31Z")); // 89 s left
    await resolveAttachments(projectId, items);
    expect(resolves().map((call) => call.handle)).toEqual([
      mediaSessionId,
      mediaSessionId,
      sessionB,
    ]);
  });
  it("bounds issuance per tab and fails closed rather than exchanging", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2030-01-01T00:00:00Z"));
    axios.post.mockImplementation(async (path) => {
      if (isExchange(path)) return exchangeResponse();
      throw { statusCode: 404 }; // every handle is refused, then dropped
    });
    for (let i = 0; i < MAX_EXCHANGES_PER_MINUTE + 3; i += 1)
      await expect(resolveAttachments(projectId, items)).rejects.toThrow();
    expect(calls().filter((call) => call.exchange)).toHaveLength(
      MAX_EXCHANGES_PER_MINUTE,
    );
    vi.setSystemTime(new Date("2030-01-01T00:01:01Z"));
    await expect(resolveAttachments(projectId, items)).rejects.toThrow();
    expect(calls().filter((call) => call.exchange)).toHaveLength(
      MAX_EXCHANGES_PER_MINUTE + 1,
    );
  });
  it("keeps the working handle when a fresh request hits the issuance bound", async () => {
    serve();
    await resolveAttachments(projectId, items);
    for (let i = 1; i < MAX_EXCHANGES_PER_MINUTE; i += 1)
      await resolveAttachments(projectId, items, undefined, { fresh: true });
    expect(calls().filter((call) => call.exchange)).toHaveLength(
      MAX_EXCHANGES_PER_MINUTE,
    );
    // Refused issuance: no exchange, and the tab's handle survives it.
    await expect(
      resolveAttachments(projectId, items, undefined, { fresh: true }),
    ).rejects.toThrow("Attachment unavailable");
    const [result] = await resolveAttachments(projectId, items);
    expect(result.gateway_path).toContain(`/${mediaSessionId}/`);
    expect(calls().filter((call) => call.exchange)).toHaveLength(
      MAX_EXCHANGES_PER_MINUTE,
    );
  });
  it("treats 429 as unavailable without retrying the exchange", async () => {
    axios.post.mockRejectedValue({ statusCode: 429, retryAfterSeconds: 60 });
    await expect(resolveAttachments(projectId, items)).rejects.toThrow(
      "Attachment unavailable",
    );
    expect(axios.post).toHaveBeenCalledTimes(1);
  });
  it("never caches a failed exchange", async () => {
    let minted = 0;
    axios.post.mockImplementation(async (path, body, config) => {
      if (isExchange(path)) {
        minted += 1;
        if (minted === 1) throw { statusCode: 503 };
        return exchangeResponse(sessionB);
      }
      return {
        data: {
          items: [
            {
              ...grant,
              gateway_path: gatewayPath(
                occurrenceId,
                "g",
                config.headers[HANDLE],
              ),
            },
          ],
        },
      };
    });
    await expect(resolveAttachments(projectId, items)).rejects.toThrow();
    const [result] = await resolveAttachments(projectId, items);
    expect(result.gateway_path).toContain(`/${sessionB}/`);
  });
  it("shares one in-flight exchange; one caller's abort cancels only that caller", async () => {
    const held = deferred();
    let exchangeSignal;
    axios.post.mockImplementation(async (path, body, config) => {
      if (isExchange(path)) {
        exchangeSignal = config.signal;
        await held.promise;
        return exchangeResponse();
      }
      return {
        data: {
          items: [
            {
              ...grant,
              gateway_path: gatewayPath(
                occurrenceId,
                "g",
                config.headers[HANDLE],
              ),
            },
          ],
        },
      };
    });
    const leaving = new AbortController();
    const first = resolveAttachments(projectId, items, leaving.signal);
    const second = resolveAttachments(projectId, items);
    leaving.abort();
    await expect(first).rejects.toThrow("Attachment unavailable");
    held.resolve();
    const [result] = await second;
    expect(result.gateway_path).toContain(`/${mediaSessionId}/`);
    expect(exchangeSignal.aborted).toBe(false);
    expect(calls().map((call) => call.exchange)).toEqual([true, false]);
  });
});

describe("explicit exchange-to-resolve binding under races", () => {
  it("keeps equal-expiry, interleaved pairs on their own handles", async () => {
    // Two sessions with identical expiry: no timestamp or "newest" tie-break.
    const sameExpiry =
      exchangeResponse().headers["x-attachment-media-expires-at"];
    const heldResolve = deferred();
    const expiries = [];
    let minted = 0;
    axios.post.mockImplementation(async (path, body, config) => {
      if (isExchange(path)) {
        const response = exchangeResponse([mediaSessionId, sessionB][minted++]);
        response.headers["x-attachment-media-expires-at"] = sameExpiry;
        expiries.push(response.headers["x-attachment-media-expires-at"]);
        return response;
      }
      const handle = config.headers[HANDLE];
      if (handle === mediaSessionId) await heldResolve.promise;
      return {
        data: {
          items: body.items.map(({ occurrence_id }) => ({
            ...grant,
            occurrence_id,
            gateway_path: gatewayPath(occurrence_id, "g", handle),
          })),
        },
      };
    });
    const first = resolveAttachments(projectId, items);
    await vi.waitFor(() => expect(resolves()).toHaveLength(1));
    // B exchanges and resolves completely between A's resolve and its reply.
    const [second] = await resolveAttachments(projectId, items, undefined, {
      fresh: true,
    });
    heldResolve.resolve();
    const [firstResult] = await first;
    expect(expiries).toHaveLength(2);
    expect(expiries[0]).toBe(expiries[1]);
    expect(firstResult.gateway_path).toContain(`/${mediaSessionId}/`);
    expect(second.gateway_path).toContain(`/${sessionB}/`);
    expect(resolves().map((call) => call.handle)).toEqual([
      mediaSessionId,
      sessionB,
    ]);
  });
  it("rejects a reply that answers one pair with the other pair's session", async () => {
    let minted = 0;
    axios.post.mockImplementation(async (path, body) => {
      if (isExchange(path))
        return exchangeResponse([mediaSessionId, sessionB][minted++]);
      // Cross-wired: always answers with B's session.
      return {
        data: {
          items: body.items.map(({ occurrence_id }) => ({
            ...grant,
            occurrence_id,
            gateway_path: gatewayPath(occurrence_id, "g", sessionB),
          })),
        },
      };
    });
    await expect(resolveAttachments(projectId, items)).rejects.toThrow(
      "Attachment unavailable",
    );
    const [result] = await resolveAttachments(projectId, items);
    expect(result.gateway_path).toContain(`/${sessionB}/`);
  });
  it("discards an exchange that completes after a token refresh, out of order", async () => {
    const heldA = deferred();
    axios.post.mockImplementation(async (path, body, config) => {
      if (isExchange(path)) {
        if (config.headers.Authorization === "Bearer synthetic-access-a") {
          await heldA.promise;
          return exchangeResponse(mediaSessionId);
        }
        return exchangeResponse(sessionB);
      }
      return {
        data: {
          items: [
            {
              ...grant,
              gateway_path: gatewayPath(
                occurrenceId,
                "g",
                config.headers[HANDLE],
              ),
            },
          ],
        },
      };
    });
    const late = resolveAttachments(projectId, items);
    await vi.waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    signIn("synthetic-access-b"); // a refresh rewrote the tab's bearer
    const [fresh] = await resolveAttachments(projectId, items);
    heldA.resolve(); // A's exchange completes last
    const [retried] = await late;
    expect(fresh.gateway_path).toContain(`/${sessionB}/`);
    expect(retried.gateway_path).toContain(`/${sessionB}/`);
    // A's handle never reached any resolve, under either bearer.
    expect(resolves()).toEqual([
      expect.objectContaining({
        bearer: "Bearer synthetic-access-b",
        handle: sessionB,
      }),
      expect.objectContaining({
        bearer: "Bearer synthetic-access-b",
        handle: sessionB,
      }),
    ]);
    expect(captureMediaContext().authorization).toBe(
      "Bearer synthetic-access-b",
    );
  });
  it("discards a resolve reply that lands after an organization switch", async () => {
    const heldReply = deferred();
    let minted = 0;
    axios.post.mockImplementation(async (path, body, config) => {
      if (isExchange(path))
        return exchangeResponse([mediaSessionId, sessionB][minted++]);
      const handle = config.headers[HANDLE];
      if (handle === mediaSessionId) await heldReply.promise;
      return {
        data: {
          items: [
            { ...grant, gateway_path: gatewayPath(occurrenceId, "g", handle) },
          ],
        },
      };
    });
    const pendingResult = resolveAttachments(projectId, items);
    await vi.waitFor(() => expect(resolves()).toHaveLength(1));
    signIn("synthetic-access-a", orgB);
    heldReply.resolve();
    const [result] = await pendingResult;
    // The old organization's grant was never returned; the pair was redone
    // with a new exchange under the new organization.
    expect(result.gateway_path).toContain(`/${sessionB}/`);
    expect(calls()).toEqual([
      expect.objectContaining({ exchange: true, organization: scopeOrg() }),
      expect.objectContaining({
        handle: mediaSessionId,
        organization: scopeOrg(),
      }),
      expect.objectContaining({ exchange: true, organization: orgB }),
      expect.objectContaining({ handle: sessionB, organization: orgB }),
    ]);
  });
  it("keeps a refused-session request from discarding the next context's handle", async () => {
    const heldA = deferred();
    axios.post.mockImplementation(async (path, body, config) => {
      if (isExchange(path)) {
        if (config.headers.Authorization === "Bearer synthetic-access-a") {
          await heldA.promise;
          return exchangeResponse(mediaSessionId);
        }
        return exchangeResponse(sessionB);
      }
      return {
        data: {
          items: [
            {
              ...grant,
              gateway_path: gatewayPath(
                occurrenceId,
                "g",
                config.headers[HANDLE],
              ),
            },
          ],
        },
      };
    });
    const recovering = resolveAttachments(projectId, items, undefined, {
      fresh: true,
    });
    await vi.waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    signIn("synthetic-access-b");
    await resolveAttachments(projectId, items); // B's session is now in use
    heldA.resolve();
    const [result] = await recovering;
    // The retried pair runs under B on B's handle; it mints nothing more.
    expect(result.gateway_path).toContain(`/${sessionB}/`);
    expect(calls().filter((call) => call.exchange)).toHaveLength(2);
  });
  it("applies nothing after logout: the late reply is dropped and no request follows", async () => {
    const heldReply = deferred();
    serve();
    axios.post.mockImplementationOnce(async () => exchangeResponse());
    axios.post.mockImplementationOnce(async () => {
      await heldReply.promise;
      return { data: { items: [grant] } };
    });
    const pendingResult = resolveAttachments(projectId, items);
    await vi.waitFor(() => expect(resolves()).toHaveLength(1));
    axios.defaults.headers.common = {}; // setSession(null)
    resetMediaSessions(); // resetAllUserStores on logout
    heldReply.resolve();
    await expect(pendingResult).rejects.toThrow("Attachment unavailable");
    expect(axios.post).toHaveBeenCalledTimes(2);
  });
});

function scopeOrg() {
  return authDefaults().headers.common["X-Organization-Id"];
}

describe("selected recording preview", () => {
  const item = { reference: { occurrence_id: occurrenceId } };
  const ready = {
    parent,
    occurrence_id: occurrenceId,
    state: "ready",
    manifest: standaloneManifest,
  };
  it("uses a pinned same-origin read and never exchanges or resolves just to discover a manifest", async () => {
    axios.get.mockResolvedValue({ data: ready });
    await expect(
      readRecordingPreview(projectId, parent, item),
    ).resolves.toEqual(ready);
    expect(axios.get.mock.calls[0][0]).toBe(
      `/tracer/v1/projects/${projectId}/attachments/occurrences/${occurrenceId}/recording-preview`,
    );
    expect(axios.get.mock.calls[0][1]).toMatchObject({
      baseURL: window.location.origin,
      params: parent,
    });
    expect(axios.post).not.toHaveBeenCalled();
  });
  it("accepts the first segment as anchor only on the selected manifest", async () => {
    const selected = {
      recording_id: mixedManifest.recording_id,
      reference: { occurrence_id: mixedManifest.segments[1].occurrence_id },
    };
    axios.get.mockResolvedValue({
      data: { ...ready, manifest: mixedManifest },
    });
    await expect(
      readRecordingPreview(projectId, parent, selected),
    ).resolves.toMatchObject({ occurrence_id: occurrenceId });
    for (const foreign of [
      { ...selected, recording_id: occurrenceId },
      { ...selected, reference: { occurrence_id: projectId } },
    ])
      await expect(
        readRecordingPreview(projectId, parent, foreign),
      ).rejects.toThrow();
  });
  it("drops a wrong-parent response, a substituted occurrence and a reply after identity replacement", async () => {
    for (const p of [
      { ...ready, parent: { ...parent, span_id: "1111111111111111" } },
      { state: "pending", parent, occurrence_id: projectId },
    ]) {
      axios.get.mockResolvedValue({ data: p });
      await expect(
        readRecordingPreview(projectId, parent, item),
      ).rejects.toThrow();
    }
    axios.get.mockImplementation(async () => {
      signIn("changed");
      return { data: ready };
    });
    await expect(
      readRecordingPreview(projectId, parent, item),
    ).rejects.toThrow();
  });
});

it.each([
  [{ statusCode: 401 }, false],
  [{ statusCode: 403 }, false],
  [{ statusCode: 404 }, false],
  [{ statusCode: 410 }, false],
  [{ statusCode: 429 }, true],
  [{ statusCode: 503 }, true],
  [{ transportCode: "ERR_NETWORK" }, true],
])(
  "classifies the app interceptor result without retrying a lost scope",
  async (error, retryable) => {
    axios.get.mockRejectedValue(error);
    await expect(
      readRecordingPreview(projectId, parent, {
        reference: { occurrence_id: occurrenceId },
      }),
    ).rejects.toMatchObject({ retryable });
  },
);
