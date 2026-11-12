import { describe, expect, it } from "vitest";

import {
  classify,
  readManagedFailure,
  readManagedRefusal,
} from "../managedRefusals";

const envelope = (status, code, metadata = {}, headers = {}) => ({
  status_code: status,
  guardrail_headers: headers,
  body: {
    error: { code, metadata, message: "Untrusted server prose: retry now" },
  },
});
const spend = (metadata, retryAfter) =>
  readManagedRefusal(
    envelope(429, "managed_spend_velocity", metadata, {
      "Retry-After": retryAfter,
    }),
  );
const noWait = (mapped) => {
  expect(mapped.category).toBe("rate_limit");
  expect(mapped.action).toEqual({
    label: "Review the spending limit with an administrator",
  });
  expect(mapped.body).not.toMatch(/try again|wait|Retry-After|Untrusted/i);
  expect(mapped.body).toContain("not retry automatically");
};

describe("R2-F0003 / R2-F0101: spend-window evidence gates", () => {
  it.each(["1", "9", " 12 ", 2])(
    "permits manual waiting with positive delta %j",
    (retryAfter) => {
      const mapped = spend(
        { remedy_hint: "retry", limit_source: "agentcc_spend_guard" },
        retryAfter,
      );
      expect(mapped.action).toEqual({ label: "Try again after waiting" });
      expect(mapped.body).toContain(`Retry-After: ${Number(retryAfter)}.`);
      expect(mapped.body).toContain("same billing source");
      expect(mapped.body).toContain("separate manual choice");
      expect(mapped.body).toContain("not retry automatically");
    },
  );

  it.each(
    [
      undefined,
      null,
      "",
      "0",
      0,
      "-1",
      -1,
      "1.5",
      1.5,
      "1e2",
      "NaN",
      "Infinity",
      Infinity,
      "9007199254740992",
      true,
      [],
      {},
      ["9"],
    ].map((value) => [value]),
  )("does not promise waiting for invalid/missing delta %j", (retryAfter) => {
    noWait(spend({ remedy_hint: "retry", retryAfter: "9" }, retryAfter));
  });

  it.each(
    [undefined, null, "", "Retry", "contact_admin", true, {}, ["retry"]].map(
      (value) => [value],
    ),
  )("requires the exact hint, not %j", (remedy_hint) =>
    noWait(spend({ remedy_hint }, "9")),
  );

  it.each(
    ["future_reason", "minute_window_full", "toString", 0, false, {}, []].map(
      (value) => [value],
    ),
  )("does not reinterpret unknown reason %j as a full window", (reason) =>
    noWait(spend({ reason, remedy_hint: "retry" }, "9")),
  );

  it.each([
    "request_exceeds_minute_cap",
    "request_exceeds_hour_cap",
    "new_org_daily_cap",
  ])("keeps terminal %s ahead of conflicting retry signals", (reason) => {
    const mapped = spend({ reason, remedy_hint: "retry" }, "9");
    expect(mapped.action.label).toBe(
      reason === "new_org_daily_cap"
        ? "Contact support"
        : "Lower max_tokens or reduce the request",
    );
    expect(mapped.body).not.toMatch(/wait|try again|Retry-After/i);
  });

  it("does not borrow hints or retry headers from the body or outer metadata", () => {
    const result = envelope(429, "managed_spend_velocity");
    result.metadata = { remedy_hint: "retry" };
    result.body.headers = { "Retry-After": "9" };
    result.body.error.metadata.retryAfter = "9";
    noWait(readManagedRefusal(result));
  });
});

describe("R2-F0015: enforceable cap remedy", () => {
  it.each(["payment_required", "invalid_request_error"])(
    "covers producer type %s without bypassing the cap",
    (type) => {
      const result = envelope(402, "call_cap_unenforceable");
      result.body.error.type = type;
      const mapped = readManagedRefusal(result);
      expect(mapped.category).toBe("agent_budget");
      expect(mapped.action).toEqual({
        label: "Choose a priced model or review the cap policy",
      });
      expect(mapped.body).toMatch(/priced model with an enforceable bound/);
      expect(mapped.body).toMatch(/authorized administrator/);
      expect(mapped.body).toMatch(/identity, route or cap policy/);
      expect(mapped.body).toContain("do not bypass the cap");
      expect(mapped.body).toContain("not retry automatically");
    },
  );
});

describe("R2-F0020 / R2-F0134 / R2-F0137: budget state remedies", () => {
  it("offers one manual attempt after waiting only for the known transient variant", () => {
    const mapped = readManagedRefusal(
      envelope(503, "budget_state_unavailable", {
        reason: "redis_unavailable",
        remedy_hint: "retry",
        scope: "run",
      }),
    );
    expect(mapped.category).toBe("billing_unavailable");
    expect(mapped.action).toEqual({ label: "Try again once after waiting" });
    expect(mapped.body).toContain("same billing source");
    expect(mapped.body).toContain("If it still fails, stop");
    expect(mapped.body).toContain("administrator");
    expect(mapped.body).not.toContain("Retry-After");
    expect(mapped.body).toContain("not retry automatically");
  });

  it.each([
    {},
    { remedy_hint: "retry" },
    { reason: "redis_unavailable" },
    { reason: "redis_unavailable", remedy_hint: "contact_admin" },
    { reason: "missing_activation", remedy_hint: "retry" },
    { reason: "stale_policy", remedy_hint: "retry" },
    { reason: "future_reason", remedy_hint: "retry" },
    { cap_scope: "agent", cap_kind: "daily", policy_revision: 1 },
  ])("keeps unproven/persistent state administrator-led: %j", (metadata) => {
    const mapped = readManagedRefusal(
      envelope(503, "budget_state_unavailable", metadata, {
        "Retry-After": "9",
      }),
    );
    expect(mapped.action).toEqual({
      label: "Ask an administrator to check budget state",
    });
    expect(mapped.body).toContain("missing activation");
    expect(mapped.body).toContain("stale policy");
    expect(mapped.body).toContain("durable budget state");
    expect(mapped.body).not.toMatch(/try again|Retry-After|Untrusted/i);
    expect(mapped.body).toContain("not retry automatically");
  });
});

describe("R2-F0107: no-code refusals fail closed", () => {
  it.each([400, 401, 402, 403, 404, 409, 429, 500, 503, 504])(
    "keeps HTTP %i generic regardless of body prose or hints",
    (status) => {
      for (const code of [undefined, null, "", false, 0]) {
        const mapped = readManagedRefusal(
          envelope(status, code, { remedy_hint: "retry", dispatched: false }),
        );
        expect(mapped).toMatchObject({
          category: "other",
          title: "Request failed",
          action: null,
        });
        expect(mapped.body).toContain("not retry automatically");
        expect(mapped.body).not.toMatch(
          /top up|rotate|billing source|Untrusted|retry now/i,
        );
      }
      expect(readManagedRefusal({ status_code: status })).toMatchObject({
        category: "other",
        action: null,
      });
    },
  );
});

describe("separate application validation failures", () => {
  it("leaves the recognized Django validation envelope to the existing safe-message reader", () => {
    const body = {
      status: false,
      message: "Choose a model first.",
      result: "Choose a model first.",
    };
    expect(readManagedFailure({ ...body, statusCode: 400 })).toBeNull();
    expect(
      readManagedFailure({ response: { status: 400, data: body } }),
    ).toBeNull();
    expect(
      readManagedFailure({ ...body, statusCode: 503, error: {} }),
    ).toMatchObject({ category: "other", action: null });
  });
});

describe("R2-F0158 and forward managed model rows", () => {
  it("retains the typed 403 permission_error unpriced remedy without a replay action", () => {
    const result = envelope(403, "managed_model_unpriced", {
      reason: "unpriced",
    });
    result.body.error.type = "permission_error";
    const mapped = readManagedRefusal(result);
    expect(mapped.category).toBe("unpriced_model");
    expect(mapped.action).toEqual({
      label: "Choose a priced model or use your own provider key",
    });
    expect(mapped.body).not.toContain("Untrusted");
  });

  it("shows exact provider-prefixed candidate IDs as data, not links or an automatic choice", () => {
    const candidates = [
      "openai/model-a",
      "vendor-b/team/model-b:1",
      "openai/model-a",
      "short-name",
      "https://bad.invalid/model",
      "vendor/<script>",
      "vendor/../model",
      null,
      {},
      42,
    ];
    const result = envelope(400, "managed_model_ambiguous", { candidates });
    result.body.error.type = "invalid_request_error";
    const mapped = readManagedRefusal(result);
    expect(mapped).toMatchObject({
      category: "other",
      title: "Managed model name is ambiguous",
      action: { label: "Choose an exact provider-prefixed model ID" },
      candidates: ["openai/model-a", "vendor-b/team/model-b:1"],
    });
    expect(mapped.body).toContain("not retry automatically");
    expect(candidates).toHaveLength(10);
    expect(mapped.action).not.toHaveProperty("href");
  });

  it.each([undefined, null, "openai/model-a", {}, []].map((value) => [value]))(
    "keeps model-selection guidance without inventing candidates: %j",
    (candidates) => {
      const mapped = classify(400, "managed_model_ambiguous", { candidates });
      expect(mapped.candidates).toEqual([]);
      expect(mapped.action.label).toBe(
        "Choose an exact provider-prefixed model ID",
      );
    },
  );

  it("does not use candidates on the wrong status, another code or outer metadata", () => {
    expect(
      classify(403, "managed_model_ambiguous", {
        candidates: ["openai/model-a"],
      }).action,
    ).toBeNull();
    const result = envelope(400, "managed_model_ambiguous");
    result.metadata = { candidates: ["openai/model-a"] };
    expect(readManagedRefusal(result).candidates).toEqual([]);
    expect(
      classify(400, "unknown", { candidates: ["openai/model-a"] }),
    ).not.toHaveProperty("candidates");
  });
});

describe("R2-F0162 / R2-F0169: response-header evidence only", () => {
  it.each([
    ["true", true],
    ["false", false],
    [undefined, "unknown"],
    ["", "unknown"],
    ["false, true", "unknown"],
    ["garbage", "unknown"],
    [false, "unknown"],
    [0, "unknown"],
  ])("reads dispatched %j without truthiness coercion", (value, dispatched) => {
    const result = envelope(
      504,
      "gateway_timeout",
      { dispatched: !dispatched, request_id: "body-id" },
      {
        "X-AgentCC-Dispatched": value,
        "x-AGENTCC-request-ID": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
      },
    );
    result.dispatched = false;
    result.request_id = "outer-id";
    result.body.dispatched = false;
    expect(readManagedRefusal(result).evidence).toEqual({
      dispatched,
      requestId: "01ARZ3NDEKTSV4RRFFQ69G5FAV",
    });
  });

  it.each([false, true, "false", "true", null])(
    "ignores body-only dispatched %j and body request IDs",
    (dispatched) => {
      const result = envelope(503, "budget_state_unavailable", {
        dispatched,
        request_id: "metadata-id",
      });
      result.body.dispatched = dispatched;
      result.body.request_id = "body-id";
      result.body.headers = {
        "x-agentcc-dispatched": "false",
        "x-agentcc-request-id": "body-header-id",
      };
      result.dispatched = dispatched;
      expect(readManagedRefusal(result).evidence).toEqual({
        dispatched: "unknown",
        requestId: null,
      });
    },
  );

  it("reads native Headers, raw Axios and normalized responseHeaders", () => {
    const headers = new Headers({
      "x-agentcc-dispatched": "false",
      "x-agentcc-request-id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
    });
    const result = envelope(
      503,
      "budget_state_unavailable",
      { dispatched: true },
      headers,
    );
    const expected = {
      dispatched: false,
      requestId: "01ARZ3NDEKTSV4RRFFQ69G5FAV",
    };
    expect(readManagedRefusal(result).evidence).toEqual(expected);
    expect(
      readManagedFailure({
        response: { status: 503, data: result.body, headers },
      }).evidence,
    ).toEqual(expected);
    expect(
      readManagedFailure({
        statusCode: 503,
        ...result.body,
        responseHeaders: headers,
      }).evidence,
    ).toEqual(expected);
  });

  it("does not substitute relay HTTP headers for absent upstream result headers", () => {
    const result = envelope(503, "budget_state_unavailable", {
      dispatched: false,
    });
    expect(
      readManagedFailure({
        response: {
          status: 503,
          data: { result },
          headers: {
            "x-agentcc-dispatched": "false",
            "x-agentcc-request-id": "relay-id",
          },
        },
      }).evidence,
    ).toEqual({ dispatched: "unknown", requestId: null });
  });

  it.each(["", " ", "bad\nheader", [], {}, 42].map((value) => [value]))(
    "does not display malformed request IDs: %j",
    (value) => {
      const mapped = readManagedRefusal(
        envelope(
          504,
          "gateway_timeout",
          { request_id: "body-id" },
          { "x-agentcc-request-id": value },
        ),
      );
      expect(mapped.evidence.requestId).toBeNull();
    },
  );
});
