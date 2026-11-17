import { describe, expect, it } from "vitest";

import { readManagedFailure, readManagedRefusal } from "../managedRefusals";
import {
  readTryResult,
  readStreamResult,
} from "../../models/detail/modelFacts";
import { interpretTestResult } from "../../guardrails/test/interpret-result";
import {
  REMEDIES,
  blockedWire,
  directFailure,
  leakedCopy,
  refusalWire,
} from "./guardrailRefusalR7Fixtures";

function expectLocal(refusal, guidance) {
  expect(refusal).not.toBeNull();
  expect(refusal.body).toContain(guidance);
  expect(JSON.stringify(refusal)).not.toMatch(leakedCopy);
  expect(refusal.evidence).toEqual({ dispatched: "unknown", requestId: null });
}

describe("r7 body type is not guardrail evidence", () => {
  it.each(REMEDIES)("maps %s through every error reader", (code, guidance) => {
    const result = refusalWire(code);
    const readers = [
      readManagedRefusal(result),
      readManagedRefusal({ ...result, body: JSON.stringify(result.body) }),
      readTryResult(result).refusal,
      readStreamResult([{ event: "result", data: result }]).refusal,
      readManagedFailure(directFailure(result)),
      readManagedFailure(directFailure(result, true)),
      interpretTestResult(result).refusal,
    ];
    readers.forEach((refusal) => expectLocal(refusal, guidance));
    expect(readTryResult(result).blocked).toBe(false);
    expect(interpretTestResult(result).outcome).toBe("failed");
  });

  it.each([false, undefined, "true", 1])(
    "requires a strict relay block boolean, not %s or a 403 body type",
    (blocked) => {
      const result = refusalWire("permission_denied", {
        status_code: 403,
        blocked,
      });
      expectLocal(readManagedRefusal(result), "access policy");
      expect(readTryResult(result).blocked).toBe(false);
      expect(interpretTestResult(result).outcome).toBe("failed");
    },
  );

  it.each([400, 401, 402, 404, 429, 500, 503])(
    "requires block status 403, not %s even with a relay block flag",
    (status_code) => {
      const result = refusalWire("unknown_future_code", {
        status_code,
        blocked: true,
      });
      expectLocal(
        readManagedRefusal(result),
        "The gateway refused the request.",
      );
      expect(readTryResult(result).blocked).toBe(false);
      expect(interpretTestResult(result).outcome).toBe("failed");
    },
  );

  it.each(["unknown_future_code", "", undefined, "content_blocked"])(
    "keeps unrecognized 403 code %s value-free without relay evidence",
    (code) => {
      const result = refusalWire(code, { status_code: 403 });
      expectLocal(
        readManagedRefusal(result),
        "The gateway refused the request.",
      );
      expect(interpretTestResult(result).outcome).toBe("failed");
    },
  );

  it("does not promote body block flags or header lookalikes on direct failures", () => {
    const result = refusalWire("managed_output_ceiling_required");
    result.body.blocked = true;
    result.body.guardrail_headers = {
      "x-agentcc-guardrail-triggered": "true",
      "x-agentcc-guardrail-action": "warn",
    };
    [false, true].forEach((normalized) =>
      expectLocal(
        readManagedFailure(directFailure(result, normalized)),
        "Set a positive max_tokens value",
      ),
    );
  });

  it("does not treat warn/log headers as block evidence on a refusal", () => {
    const result = refusalWire("managed_output_ceiling_required", {
      guardrail_headers: {
        "X-Agentcc-Guardrail-Triggered": "true",
        "X-Agentcc-Guardrail-Action": "warn",
      },
    });
    expectLocal(readManagedRefusal(result), "Set a positive max_tokens value");
    expect(interpretTestResult(result).outcome).toBe("failed");
  });
});

describe("r7 published relay and header outcomes survive", () => {
  it.each(["guardrail_error", "permission_error", undefined])(
    "preserves the relay-attested 403 block regardless of body type %s",
    (type) => {
      const result = blockedWire();
      result.body.error.type = type;
      expect(readManagedRefusal(result)).toBeNull();
      expect(readTryResult(result)).toMatchObject({
        blocked: true,
        refusal: null,
        error: result.body.error.message,
      });
      expect(interpretTestResult(result)).toMatchObject({
        outcome: "blocked",
        rule: "keyword-blocklist",
        reason: "Blocked keywords found: internal only",
      });
    },
  );

  it.each([
    ["warn", "warned"],
    ["log", "logged"],
    [undefined, "triggered"],
  ])("reads non-blocking %s from response headers", (action, outcome) => {
    const result = {
      status_code: 200,
      // Conflicting relay aliases must not override authoritative headers.
      warned: false,
      guardrail_action: action === "warn" ? "log" : "warn",
      triggered_checks: ["private-value-r7"],
      body: { choices: [{ message: { content: "A safe answer." } }] },
      guardrail_headers: {
        "X-Agentcc-Guardrail-Triggered": "true",
        "X-Agentcc-Guardrail-Action": action,
        "X-Agentcc-Guardrails-Triggered": "prompt-injection",
      },
    };
    expect(interpretTestResult(result)).toMatchObject({
      outcome,
      checks: ["prompt-injection"],
      answer: "A safe answer.",
    });
  });

  it.each([
    { headers: {} },
    { headers: { "x-agentcc-guardrail-triggered": "false" } },
    { headers: { "x-agentcc-guardrail-triggered": true } },
    { headers: { "x-agentcc-guardrail-triggered": "true,false" } },
    {
      headers: {
        "x-agentcc-guardrail-triggered": "true",
        "X-Agentcc-Guardrail-Triggered": "true",
      },
    },
  ])(
    "rejects missing/malformed/duplicate triggered evidence: %j",
    ({ headers }) => {
      expect(
        interpretTestResult({
          status_code: 200,
          warned: true,
          blocked: true,
          guardrail_action: "warn",
          guardrail_headers: headers,
          body: { error: { type: "guardrail_error" } },
        }).outcome,
      ).toBe("passed");
    },
  );

  it("keeps an empty nested header bag authoritative", () => {
    expect(
      interpretTestResult({
        status_code: 200,
        warned: true,
        guardrail_headers: {},
        headers: { "x-agentcc-guardrail-triggered": "true" },
      }).outcome,
    ).toBe("passed");
  });
});
