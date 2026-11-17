import { describe, expect, it, vi } from "vitest";

import * as shared from "../index";
import * as omegaClient from "../omegaClient";
import { createOmegaClient, resolveOmegaApiBase } from "../omegaClient";
import { OMEGA_STATE, OmegaError } from "../omegaStates";

const jsonResponse = (body, status = 200, headers = {}) =>
  new Response(body === null ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });

function setup(responses, { mint, sleep } = {}) {
  let issued = 0;
  const mintAssertion =
    mint ||
    vi.fn(async () => ({
      token: `omega_delegate_a.b.${++issued}`,
      expiresAt: 0,
    }));
  const queue = [...responses];
  const fetchImpl = vi.fn(async () => {
    const next = queue.shift();
    if (next instanceof Error || next?.name === "AbortError") throw next;
    return typeof next === "function" ? next() : next;
  });
  const client = createOmegaClient({
    basePath: "/omega/api",
    fetchImpl,
    mintAssertion,
    ...(sleep ? { sleep } : {}),
  });
  return { client, fetchImpl, mintAssertion };
}

describe("omegaFetch", () => {
  it("sends a freshly minted assertion per request and nothing from the host session", async () => {
    const { client, fetchImpl, mintAssertion } = setup([
      jsonResponse({ items: [] }),
      jsonResponse({ items: [1] }),
    ]);
    const first = await client.omegaFetch("/workspace/deployments", {
      query: { limit: 5, cursor: undefined },
    });
    const second = await client.omegaFetch("/workspace/deployments");
    expect(first).toMatchObject({ ok: true, status: 200, data: { items: [] } });
    expect(second.data).toEqual({ items: [1] });
    expect(mintAssertion).toHaveBeenCalledTimes(2);
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("/omega/api/workspace/deployments?limit=5");
    expect(init.credentials).toBe("omit");
    expect(init.headers).toEqual({
      Accept: "application/json",
      Authorization: "Bearer omega_delegate_a.b.1",
    });
    expect(fetchImpl.mock.calls[1][1].headers.Authorization).toBe(
      "Bearer omega_delegate_a.b.2",
    );
  });

  it("serialises json bodies and keeps caller headers such as Idempotency-Key", async () => {
    const { client, fetchImpl } = setup([jsonResponse({ id: "dep-1" }, 202)]);
    const result = await client.omegaFetch(
      "/workspace/deployments/dep-1/rollback",
      {
        method: "post",
        json: { expectedVersion: 3, targetVersion: 1 },
        headers: { "Idempotency-Key": "rb-1" },
      },
    );
    expect(result).toMatchObject({
      ok: true,
      status: 202,
      data: { id: "dep-1" },
    });
    const init = fetchImpl.mock.calls[0][1];
    expect(init.method).toBe("POST");
    expect(init.body).toBe('{"expectedVersion":3,"targetVersion":1}');
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(init.headers["Idempotency-Key"]).toBe("rb-1");
  });

  it("refuses anything that is not a gateway route", async () => {
    const { client, fetchImpl } = setup([]);
    for (const bad of [
      "https://evil.example/x",
      "//evil.example/x",
      "workspace/x",
      "/workspace/../os/agents",
      "/workspace/./x",
      "",
      null,
    ]) {
      await expect(client.omegaFetch(bad)).rejects.toThrow(TypeError);
    }
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("maps gateway and EE refusals to tab states with their codes", async () => {
    const cases = [
      [
        jsonResponse({ error: "route_not_allowed" }, 403),
        OMEGA_STATE.REFUSED,
        "route_not_allowed",
      ],
      [
        jsonResponse({ error: "omega_session_unavailable" }, 401),
        OMEGA_STATE.REFUSED,
        "omega_session_unavailable",
      ],
      [
        jsonResponse(
          { error: { code: "sandbox_not_found", message: "No such sandbox" } },
          404,
        ),
        OMEGA_STATE.NOT_FOUND,
        "sandbox_not_found",
      ],
      [
        jsonResponse({ error: "omega_remote_unreachable" }, 502),
        OMEGA_STATE.UNAVAILABLE,
        "omega_remote_unreachable",
      ],
      [
        jsonResponse(
          { code: "version_conflict", message: "Expected version 3" },
          409,
        ),
        OMEGA_STATE.ERROR,
        "version_conflict",
      ],
    ];
    for (const [response, state, code] of cases) {
      const { client } = setup([response]);
      const result = await client.omegaFetch("/workspace/sandboxes/sbx-1");
      expect(result).toMatchObject({
        ok: false,
        state,
        code,
        status: response.status,
      });
      expect(typeof result.message).toBe("string");
    }
  });

  it("reports an unreachable gateway as unavailable and lets aborts reject", async () => {
    const { client } = setup([new TypeError("Failed to fetch")]);
    expect(await client.omegaFetch("/workspace/sandboxes")).toMatchObject({
      ok: false,
      state: OMEGA_STATE.UNAVAILABLE,
      code: "network_error",
    });
    const abort = new DOMException("aborted", "AbortError");
    const aborted = setup([abort]);
    await expect(
      aborted.client.omegaFetch("/workspace/sandboxes"),
    ).rejects.toBe(abort);
  });

  it("returns mint refusals without calling the gateway", async () => {
    const mint = vi.fn(async () => {
      throw new OmegaError({
        state: OMEGA_STATE.REFUSED,
        status: 403,
        code: "viewer",
      });
    });
    const { client, fetchImpl } = setup([], { mint });
    expect(await client.omegaFetch("/workspace/deployments")).toMatchObject({
      ok: false,
      state: OMEGA_STATE.REFUSED,
      status: 403,
    });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("refreshes a stale assertion once for retry-safe requests only", async () => {
    const stale = () =>
      jsonResponse(
        { error: "omega_assertion_rejected", reason: "expired_assertion" },
        401,
      );
    const getter = setup([stale(), jsonResponse({ items: [] })]);
    expect(
      await getter.client.omegaFetch("/workspace/sandboxes"),
    ).toMatchObject({ ok: true });
    expect(getter.mintAssertion).toHaveBeenCalledTimes(2);

    const keyed = setup([stale(), jsonResponse({ id: "s" }, 201)]);
    const created = await keyed.client.omegaFetch("/workspace/sandboxes", {
      method: "POST",
      json: {},
      headers: { "Idempotency-Key": "k" },
    });
    expect(created).toMatchObject({ ok: true, status: 201 });

    const unkeyed = setup([stale(), jsonResponse({ id: "s" }, 201)]);
    const refused = await unkeyed.client.omegaFetch("/workspace/sandboxes", {
      method: "POST",
      json: {},
    });
    expect(refused).toMatchObject({
      ok: false,
      state: OMEGA_STATE.REFUSED,
      status: 401,
    });
    expect(unkeyed.fetchImpl).toHaveBeenCalledTimes(1);

    const twice = setup([stale(), stale()]);
    expect(await twice.client.omegaFetch("/workspace/sandboxes")).toMatchObject(
      { ok: false, status: 401 },
    );
    expect(twice.fetchImpl).toHaveBeenCalledTimes(2);
  });

  it("omegaJson resolves data or throws a typed OmegaError", async () => {
    const { client } = setup([
      jsonResponse({ id: "dep-1" }),
      jsonResponse({ error: "route_not_allowed" }, 403),
    ]);
    await expect(
      client.omegaJson("/workspace/deployments/dep-1"),
    ).resolves.toEqual({ id: "dep-1" });
    await expect(client.omegaJson("/os/sandboxes")).rejects.toMatchObject({
      name: "OmegaError",
      state: OMEGA_STATE.REFUSED,
      status: 403,
      code: "route_not_allowed",
    });
  });

  it.each([
    ["text/html", "text/html", "<html>Proxy fallback</html>"],
    ["application/json", "application/json", "{broken json"],
    ["application/problem+json", "application/problem+json", "   "],
    ["text/plain (default) with valid JSON", null, '{"deployments":[]}'],
  ])(
    "rejects a non-empty malformed success with content-type %s",
    async (_label, contentType, body) => {
      const headers = contentType ? { "content-type": contentType } : {};
      const { client, fetchImpl } = setup([
        new Response(body, { status: 200, headers }),
      ]);
      expect(await client.omegaFetch("/workspace/deployments")).toEqual({
        ok: false,
        state: OMEGA_STATE.ERROR,
        status: 200,
        code: "malformed_response",
        reason: null,
        title: null,
        message: "Omega could not complete the request.",
        retryAfter: null,
      });
      expect(fetchImpl).toHaveBeenCalledTimes(1);
    },
  );

  it.each([
    [204, null, "application/json"],
    [200, "", "application/json"],
    [200, "", "text/html"],
  ])(
    "keeps an empty %s response successful (%s, %s)",
    async (status, body, contentType) => {
      const { client } = setup([
        new Response(body, {
          status,
          headers: { "content-type": contentType },
        }),
      ]);
      expect(await client.omegaFetch("/workspace/deployments")).toMatchObject({
        ok: true,
        status,
        data: null,
      });
    },
  );

  it.each([
    ["text/event-stream", "data: hello\n\n"],
    ["application/octet-stream", "download bytes"],
    ["text/html", "<html>fallback</html>"],
    ["application/json", "{broken json"],
  ])("leaves raw success bodies unread (%s)", async (contentType, body) => {
    const response = new Response(body, {
      headers: { "content-type": contentType },
    });
    const { client } = setup([response]);
    const result = await client.omegaFetch("/workspace/sandboxes", {
      raw: true,
    });
    expect(result).toMatchObject({ ok: true, status: 200, response });
    expect(result).not.toHaveProperty("data");
    expect(response.bodyUsed).toBe(false);
    expect(await response.text()).toBe(body);
  });

  it.each(["application/json", "text/html"])(
    "omegaJson throws OmegaError for malformed %s successes",
    async (contentType) => {
      const { client } = setup([
        new Response("not JSON", {
          status: 202,
          headers: { "content-type": contentType },
        }),
      ]);
      const result = client.omegaJson("/workspace/deployments");
      await expect(result).rejects.toBeInstanceOf(OmegaError);
      await expect(result).rejects.toMatchObject({
        state: OMEGA_STATE.ERROR,
        status: 202,
        code: "malformed_response",
        reason: null,
        title: null,
        message: "Omega could not complete the request.",
        retryAfter: null,
      });
    },
  );

  it.each([
    ["application/json", "Please try again.", "Please try again."],
    [
      "text/html",
      "<html>proxy error</html>",
      "Omega could not complete the request.",
    ],
  ])(
    "keeps the existing non-2xx body handling (%s)",
    async (contentType, body, message) => {
      const { client } = setup([
        new Response(body, {
          status: 500,
          headers: { "content-type": contentType },
        }),
      ]);
      expect(await client.omegaFetch("/workspace/deployments")).toMatchObject({
        ok: false,
        state: OMEGA_STATE.ERROR,
        status: 500,
        code: null,
        message,
      });
    },
  );

  it("accepts valid JSON strings without confusing them with non-JSON text", async () => {
    const { client } = setup([jsonResponse("a JSON string")]);
    expect(await client.omegaFetch("/workspace/deployments")).toMatchObject({
      ok: true,
      data: "a JSON string",
    });
  });

  it("only accepts a same-origin absolute base path", () => {
    expect(resolveOmegaApiBase("/omega/api")).toBe("/omega/api");
    expect(resolveOmegaApiBase("https://gateway.example/omega")).toBe(
      "/omega/api",
    );
    expect(resolveOmegaApiBase("//gateway.example")).toBe("/omega/api");
    expect(resolveOmegaApiBase(undefined)).toBe("/omega/api");
  });
});

describe("requireList", () => {
  it("exports the shared malformed-response code and list validator", () => {
    expect(omegaClient.MALFORMED_RESPONSE).toBe("malformed_response");
    expect(omegaClient.requireList).toEqual(expect.any(Function));
    expect(shared.MALFORMED_RESPONSE).toBe(omegaClient.MALFORMED_RESPONSE);
    expect(shared.requireList).toBe(omegaClient.requireList);
  });

  it("returns arrays unchanged, including a genuinely empty list", () => {
    expect(omegaClient.requireList).toEqual(expect.any(Function));
    for (const deployments of [[], [{ id: "dep-1" }]]) {
      expect(omegaClient.requireList({ deployments }, "deployments")).toBe(
        deployments,
      );
    }
  });

  it.each([
    undefined,
    null,
    {},
    [],
    "text",
    { deployments: "x" },
    { deployments: {} },
    { deployments: null },
  ])(
    "throws a typed failure rather than treating %j as an empty list",
    (body) => {
      expect(omegaClient.requireList).toEqual(expect.any(Function));
      let error;
      try {
        omegaClient.requireList(body, "deployments");
      } catch (caught) {
        error = caught;
      }
      expect(error).toBeInstanceOf(OmegaError);
      expect(error).toMatchObject({
        state: OMEGA_STATE.ERROR,
        status: null,
        code: "malformed_response",
        reason: null,
        title: null,
        message: "Omega could not complete the request.",
        retryAfter: null,
      });
    },
  );
});
