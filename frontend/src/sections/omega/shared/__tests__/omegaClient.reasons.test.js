import { describe, expect, it, vi } from "vitest";

import { createOmegaClient } from "../omegaClient";
import { OMEGA_STATE } from "../omegaStates";

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

describe("omegaFetch reason codes (L07, unit)", () => {
  it("maps each EE reason code to Future AGI copy and never shows EE's 401 or 403 text", async () => {
    const eeRefusal = (code, status, message) =>
      jsonResponse(
        { error: code, message, retryable: status === 503 },
        status,
        status === 503 ? { "retry-after": "2" } : {},
      );
    const cases = [
      [
        "assertion_invalid",
        401,
        OMEGA_STATE.REFUSED,
        "Omega couldn't confirm your sign-in",
      ],
      [
        "identity_not_connected",
        403,
        OMEGA_STATE.REFUSED,
        "Omega isn't connected for your account",
      ],
      [
        "unauthorized",
        401,
        OMEGA_STATE.REFUSED,
        "Omega couldn't confirm your access",
      ],
      [
        "host_delegation_scope_denied",
        403,
        OMEGA_STATE.REFUSED,
        "Not available from Future AGI",
      ],
    ];
    for (const [code, status, state, title] of cases) {
      const { client, fetchImpl } = setup([
        eeRefusal(code, status, "Valid Omega API key required."),
        eeRefusal(code, status, "Valid Omega API key required."),
      ]);
      const result = await client.omegaFetch("/workspace/sandboxes");
      expect(result).toMatchObject({ ok: false, state, status, code, title });
      expect(result.message).not.toMatch(/API key|Omega server|operator/);
      // Not a stale key: one request, no second mint.
      expect(fetchImpl).toHaveBeenCalledTimes(1);
    }
  });

  // Cut 5: the copy asked for a reload after the client had already sent a
  // fresh key, which a reload only repeats.
  it("says to try again in a moment and to contact support, never to reload, when the fresh key is refused too", async () => {
    for (const [code, reason] of [
      ["assertion_replayed", null],
      ["assertion_expired", null],
      ["omega_assertion_rejected", "replayed_assertion"],
    ]) {
      const { client, fetchImpl, mintAssertion } = setup([
        jsonResponse({ error: code, reason, retryable: true }, 401),
        jsonResponse({ error: code, reason, retryable: true }, 401),
      ]);
      const result = await client.omegaFetch("/workspace/sandboxes");
      expect(fetchImpl).toHaveBeenCalledTimes(2);
      expect(mintAssertion).toHaveBeenCalledTimes(2);
      expect(result).toMatchObject({
        ok: false,
        state: OMEGA_STATE.REFUSED,
        status: 401,
        code,
        title: "Omega couldn't confirm your sign-in",
        message:
          "Future AGI couldn't confirm your sign-in with Omega. Try again in a moment. If it keeps happening, contact Future AGI support.",
      });
      expect(result.message).not.toMatch(/reload/i);
    }
  });

  it("replaces a stale or out-of-date key once, for EE's codes too", async () => {
    for (const [code, status] of [
      ["assertion_expired", 401],
      ["assertion_replayed", 401],
      ["role_out_of_date", 403],
    ]) {
      const { client, fetchImpl, mintAssertion } = setup([
        jsonResponse({ error: code, retryable: true }, status),
        jsonResponse({ sandboxes: [] }),
      ]);
      expect(await client.omegaFetch("/workspace/sandboxes")).toMatchObject({
        ok: true,
        data: { sandboxes: [] },
      });
      expect(fetchImpl).toHaveBeenCalledTimes(2);
      expect(mintAssertion).toHaveBeenCalledTimes(2);
      expect(fetchImpl.mock.calls[1][1].headers.Authorization).toBe(
        "Bearer omega_delegate_a.b.2",
      );
    }
  });

  it("waits out a short 503 pause once, and reports a long one with its wait", async () => {
    const sleep = vi.fn(async () => {});
    const paused = setup(
      [
        jsonResponse(
          { error: "verification_unavailable", retryable: true },
          503,
          {
            "retry-after": "2",
          },
        ),
        jsonResponse({ ok: 1 }),
      ],
      { sleep },
    );
    expect(
      await paused.client.omegaFetch("/workspace/sandboxes"),
    ).toMatchObject({
      ok: true,
    });
    expect(sleep).toHaveBeenCalledWith(2000, undefined);

    const twice = setup(
      [
        jsonResponse({ error: "verification_unavailable" }, 503, {
          "retry-after": "2",
        }),
        jsonResponse({ error: "verification_unavailable" }, 503, {
          "retry-after": "2",
        }),
      ],
      { sleep },
    );
    expect(await twice.client.omegaFetch("/workspace/sandboxes")).toMatchObject(
      {
        ok: false,
        state: OMEGA_STATE.UNAVAILABLE,
        code: "verification_unavailable",
        retryAfter: 2,
        title: "Sign-in checks are briefly unavailable",
      },
    );
    expect(twice.fetchImpl).toHaveBeenCalledTimes(2);

    const long = setup(
      [
        jsonResponse({ error: "omega_gateway_draining" }, 503, {
          "retry-after": "30",
        }),
      ],
      { sleep: vi.fn() },
    );
    expect(await long.client.omegaFetch("/workspace/sandboxes")).toMatchObject({
      ok: false,
      code: "omega_gateway_draining",
      retryAfter: 30,
    });
    expect(long.fetchImpl).toHaveBeenCalledTimes(1);

    // A write without an Idempotency-Key is never re-sent.
    const write = setup(
      [
        jsonResponse(
          {
            error: "omega_rate_limit_unavailable",
            reason: "rate_store_unavailable",
            retryAfterSeconds: 2,
          },
          503,
          { "retry-after": "2" },
        ),
      ],
      { sleep: vi.fn() },
    );
    expect(
      await write.client.omegaFetch("/workspace/sandboxes", {
        method: "POST",
        json: {},
      }),
    ).toMatchObject({
      ok: false,
      state: OMEGA_STATE.UNAVAILABLE,
      code: "omega_rate_limit_unavailable",
      title: "Omega is pausing changes",
      retryAfter: 2,
    });
    expect(write.fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("says how long to wait when the gateway throttles", async () => {
    const { client } = setup([
      jsonResponse(
        {
          error: "omega_rate_limited",
          reason: "rate_limited",
          rateClass: "write",
          retryAfterSeconds: 12,
        },
        429,
        { "retry-after": "12" },
      ),
    ]);
    expect(
      await client.omegaFetch("/workspace/sandboxes", {
        method: "POST",
        json: {},
      }),
    ).toMatchObject({
      ok: false,
      state: OMEGA_STATE.ERROR,
      status: 429,
      retryAfter: 12,
      message:
        "This workspace is sending requests to Omega faster than its limit. Try again in 12 seconds.",
    });
  });

  it("omegaJson throws an OmegaError that carries the copy, title and reason", async () => {
    const { client } = setup([
      jsonResponse(
        { error: "host_delegation_scope_denied", reason: "role_below_floor" },
        403,
      ),
    ]);
    await expect(
      client.omegaJson("/workspace/sandboxes/settings"),
    ).rejects.toMatchObject({
      name: "OmegaError",
      state: OMEGA_STATE.REFUSED,
      code: "host_delegation_scope_denied",
      reason: "role_below_floor",
      title: "Your role doesn't allow this",
    });
  });
});

describe("omegaFetch on EE's workspace-route refusals (L07, unit, regression)", () => {
  // EE's sandbox and deployment routes: {schemaVersion, error: {code, message, detail?}}.
  const workspaceRefusal = (code, message, detail) =>
    jsonResponse(
      {
        schemaVersion: "omega-workspace-sandboxes/v1",
        error: { code, message, ...(detail ? { detail } : {}) },
      },
      403,
    );
  // EE's own sentences (workspace/sandboxes/access/creator-only.ts).
  const EE_SENTENCE = {
    view: "Only the member who created this sandbox, or a workspace owner or admin, can see it.",
    operate:
      "Only the member who created this sandbox can run commands in it, change its files or change its state.",
    destroy:
      "Only the member who created this sandbox, or a workspace owner or admin, can destroy it.",
  };

  it("tells an admin acting on a member's sandbox that only its creator can (verifier repro)", async () => {
    const { client, fetchImpl } = setup([
      workspaceRefusal("role_forbidden", EE_SENTENCE.operate, {
        reason: "not_creator",
        verb: "operate",
      }),
    ]);
    const result = await client.omegaFetch("/workspace/sandboxes/s1/exec", {
      method: "POST",
      json: { command: "ls" },
    });
    expect(result).toMatchObject({
      ok: false,
      state: OMEGA_STATE.REFUSED,
      status: 403,
      code: "role_forbidden",
      reason: "not_creator",
      title: "Only the sandbox's creator can do this",
    });
    expect(result.message).toMatch(
      /^Only the member who created this sandbox can run commands in it, change its files or change its state\./,
    );
    expect(result.message).not.toMatch(/only workspace owners and admins/i);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("gives each creator-only verb its own copy", async () => {
    const expected = {
      view: "This sandbox is private to its creator",
      operate: "Only the sandbox's creator can do this",
      destroy: "Only its creator or an admin can delete this sandbox",
    };
    for (const [verb, title] of Object.entries(expected)) {
      const { client } = setup([
        workspaceRefusal("role_forbidden", EE_SENTENCE[verb], {
          reason: "not_creator",
          verb,
        }),
      ]);
      const result = await client.omegaFetch("/workspace/sandboxes/s1");
      expect(result, verb).toMatchObject({ reason: "not_creator", title });
      expect(result.message).not.toMatch(/only workspace owners and admins/i);
    }
  });

  it("names owners and admins only when EE says a member's role was refused", async () => {
    const member = setup([
      workspaceRefusal(
        "role_forbidden",
        "Workspace owners and admins may create a deployment; a deployment receives the workspace secrets it references.",
        { role: "member" },
      ),
    ]);
    expect(
      await member.client.omegaFetch("/workspace/deployments", {
        method: "POST",
        json: {},
      }),
    ).toMatchObject({
      state: OMEGA_STATE.REFUSED,
      title: "Only workspace owners and admins can do this",
    });
    const bare = setup([
      workspaceRefusal("role_forbidden", "Refused for a reason we never sent."),
    ]);
    const bareResult = await bare.client.omegaFetch("/workspace/sandboxes");
    expect(bareResult).toMatchObject({ title: "Not allowed" });
    expect(bareResult.message).not.toMatch(/only workspace owners and admins/i);
  });

  it("replaces internal addresses, daemon names and assertion talk on any status (verifier repro)", async () => {
    const { client } = setup([
      jsonResponse(
        {
          error: "bad",
          message: "Assertion audience mismatch for omega-squad",
        },
        400,
      ),
      jsonResponse(
        {
          error: {
            code: "internal",
            message: "connect ECONNREFUSED 10.0.3.7:7700 (os-daemon)",
          },
        },
        500,
      ),
    ]);
    const first = await client.omegaFetch("/workspace/sandboxes");
    expect(first).toMatchObject({ ok: false, state: OMEGA_STATE.ERROR });
    expect(first.message).toBe("Omega could not complete the request.");
    const second = await client.omegaFetch("/workspace/sandboxes");
    expect(second.message).toBe("Omega could not complete the request.");
  });
});
