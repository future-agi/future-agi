import { afterEach, describe, expect, it, vi } from "vitest";

import parity from "./fixtures/GW-FE-ONEKEY-REFUSAL-PARITY-r7.json";
import {
  classify,
  readManagedFailure,
  readManagedRefusal,
} from "../managedRefusals";

const ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV";
const SPOOF = "Not charged; retry after 123456 seconds; private-value";
const wire = (status = 429, code = "managed_spend_velocity", headers = {}) => ({
  status_code: status,
  guardrail_headers: headers,
  body: { error: { code, message: SPOOF, metadata: { remedy_hint: "retry" } } },
});
const variants = (result) => [
  readManagedRefusal(result),
  readManagedRefusal({ ...result, body: JSON.stringify(result.body) }),
  readManagedFailure({
    statusCode: result.status_code,
    ...result.body,
    responseHeaders: result.guardrail_headers,
  }),
  readManagedFailure({
    response: {
      status: result.status_code,
      data: result.body,
      headers: result.guardrail_headers,
    },
  }),
  readManagedFailure({
    response: {
      status: 502,
      data: { result },
      headers: { "Retry-After": "123", "x-agentcc-request-id": ID },
    },
  }),
];
afterEach(() => vi.restoreAllMocks());

// This is the producer's entire hash-bound table, not a hand-maintained code
// subset. A future named code must fail here until it has a non-fallback map.
describe("r7 parity drift guard", () => {
  it("binds all 84 rows, including the two informational evidence rows", () => {
    expect(parity.id).toBe("GW-FE-ONEKEY-REFUSAL-PARITY-r7");
    expect(parity.rows).toHaveLength(84);
    expect(
      parity.rows.filter((row) => row.errorCode === "<response_evidence>"),
    ).toHaveLength(2);
  });
  it.each(parity.rows)(
    "maps $httpStatus / $errorCode / $consumerClassOrState",
    (row) => {
      if (row.errorCode === "<response_evidence>") {
        const refusal = readManagedRefusal(
          wire(504, "gateway_timeout", {
            "x-agentcc-dispatched": "false",
            "x-agentcc-request-id": ID,
          }),
        );
        expect(refusal.evidence).toEqual({ dispatched: false, requestId: ID });
        expect(row.consumerClassOrState).toMatch(
          /^managedRefusal\.evidence\.(dispatched|requestId)$/,
        );
        return;
      }
      const missing = row.errorCode === "<no_error.code>";
      const result = wire(row.httpStatus, missing ? undefined : row.errorCode);
      // wire's default is intentional elsewhere; missing-code rows delete it.
      if (missing) delete result.body.error.code;
      result.body.error.metadata = {};
      for (const refusal of variants(result)) {
        expect(refusal.category).toBe(row.userFacingCategory);
        expect(refusal.title).toBeTruthy();
        expect(refusal.body).toBeTruthy();
        expect(refusal.body).not.toContain(SPOOF);
        expect(refusal.body).not.toMatch(/not charged|123456|private-value/i);
        if (missing || row.errorCode === "unknown_future_code") {
          expect(refusal.title).toBe("Request failed");
          expect(refusal.action).toBeNull();
        } else {
          expect(refusal.title).not.toBe("Request failed");
          expect(refusal.action?.label).toBeTruthy();
        }
      }
    },
  );
});

describe("four forward managed request remedies", () => {
  it.each(parity.rows.slice(80))(
    "keeps $errorCode local and terminal",
    (row) => {
      const result = wire(400, row.errorCode);
      result.body.error.param = "n=private-value";
      const refusal = readManagedRefusal(result);
      expect(refusal.category).toBe("other");
      expect(refusal.body).toContain("not retry automatically");
      expect(refusal.body).not.toMatch(/private-value|123456|not charged/i);
      expect(refusal.action).not.toHaveProperty("href");
      expect(
        classify(403, row.errorCode, { message: SPOOF }).action,
      ).toBeNull();
    },
  );
  it.each([
    "n",
    "best_of",
    "max_tokens",
    "max_completion_tokens",
    "max_output_tokens",
    "max_new_tokens",
    "maxOutputTokens",
    "max_tokens_to_sample",
  ])("only displays the local parameter label %s", (param) => {
    const result = wire(400, "managed_unsupported_parameter");
    result.body.error.param = param;
    expect(readManagedRefusal(result).body).toContain(`Field: ${param}.`);
  });
  it.each(
    [null, "", " n", "n=5", "extra_body.private-value", {}, ["n"]].map(
      (value) => [value],
    ),
  )("does not echo a non-allowlisted parameter %j", (param) => {
    const result = wire(400, "managed_unsupported_parameter");
    result.body.error.param = param;
    expect(readManagedRefusal(result).body).not.toContain("Field:");
  });
});

const WAITABLE = [
  [429, "managed_spend_velocity"],
  [429, "rate_limit_exceeded"],
  [429, "budget_throttled"],
  [429, "token_rate_limit_exceeded"],
  [503, "provider_busy"],
  [409, "idempotency_in_progress"],
];
describe.each(WAITABLE)("bounded Retry-After: %i / %s", (status, code) => {
  it.each(["1", " 9 ", "86400", 12])("accepts bounded delta %j", (value) => {
    for (const refusal of variants(
      wire(status, code, { "Retry-After": value }),
    )) {
      expect(refusal.body).toContain(`Retry-After: ${Number(value)}.`);
    }
  });
  it.each(
    [
      undefined,
      null,
      "",
      " ",
      "86401",
      86401,
      -1,
      "-1",
      "1.5",
      "1e2",
      true,
      [],
      ["9"],
      {},
      "9, 9",
      "9\n",
      Infinity,
      "private-value",
    ].map((value) => [value]),
  )("rejects invalid delta %j", (value) => {
    for (const refusal of variants(
      wire(status, code, { "Retry-After": value }),
    )) {
      expect(refusal.body).not.toContain("Retry-After:");
      expect(refusal.body).not.toContain("private-value");
    }
  });
  it("converts a direct HTTP-date using ceil and rejects past/over-limit dates", () => {
    const now = Date.UTC(2026, 10, 6, 12, 0, 0, 500);
    vi.spyOn(Date, "now").mockReturnValue(now);
    const date = new Date(now + 9500).toUTCString();
    for (const refusal of variants(
      wire(status, code, { "Retry-After": date }),
    )) {
      expect(refusal.body).toContain("Retry-After: 10.");
    }
    for (const invalid of [
      new Date(now - 1000).toUTCString(),
      new Date(now + 86402000).toUTCString(),
      "2026-11-06",
      "Fri, 31 Feb 2026 12:00:10 GMT",
    ]) {
      expect(
        readManagedRefusal(wire(status, code, { "Retry-After": invalid })).body,
      ).not.toContain("Retry-After:");
    }
  });
  it("preserves zero but does not offer a filled-window wait for zero", () => {
    const refusal = readManagedRefusal(
      wire(status, code, { "Retry-After": "0" }),
    );
    if (code === "managed_spend_velocity") {
      expect(refusal.action.label).toBe(
        "Review the spending limit with an administrator",
      );
      expect(refusal.body).not.toContain("Retry-After:");
    } else expect(refusal.body).toContain("Retry-After: 0.");
  });
});

describe("header authority and no billing claims", () => {
  it("ignores all body and scalar aliases for timing, dispatch and identity", () => {
    const result = wire();
    Object.assign(result.body.error.metadata, {
      dispatched: false,
      request_id: ID,
      retry_after: 9,
      retryAfter: 9,
      retryAfterSeconds: 9,
      resets_at: "2099-01-01T00:00:00Z",
    });
    Object.assign(result.body, {
      dispatched: false,
      request_id: ID,
      headers: { "Retry-After": "9" },
      retryAfterSeconds: 9,
      requestId: ID,
    });
    for (const refusal of variants(result)) {
      expect(refusal.evidence).toEqual({
        dispatched: "unknown",
        requestId: null,
      });
      expect(refusal.action.label).toBe(
        "Review the spending limit with an administrator",
      );
      expect(refusal.body).not.toMatch(
        /Retry-After:|not charged|private-value/i,
      );
    }
  });
  it.each(["true", "false", "TRUE", " false ", false, undefined])(
    "does not infer a billing outcome from dispatch %j",
    (dispatch) => {
      for (const row of parity.rows.filter(
        (entry) => typeof entry.httpStatus === "number",
      )) {
        const result = wire(row.httpStatus, row.errorCode, {
          "x-agentcc-dispatched": dispatch,
          "x-agentcc-request-id": ID,
        });
        result.body.error.metadata.dispatched = false;
        const refusal = readManagedRefusal(result);
        expect(refusal.evidence.dispatched).toBe(
          dispatch === "true" ? true : dispatch === "false" ? false : "unknown",
        );
        expect(refusal.body).not.toMatch(
          /not charged|no charge|refunded|123456/i,
        );
      }
    },
  );
  it.each(
    [
      "header-id",
      "81ARZ3NDEKTSV4RRFFQ69G5FAV",
      ID.toLowerCase(),
      `${ID}\n`,
      "01ARZ3NDEKTSV4RRFFQ69G5FAI",
      "",
      [ID],
      null,
    ].map((value) => [value]),
  )("rejects non-r4 request identity %j", (id) => {
    const result = wire(504, "gateway_timeout", {
      "x-agentcc-request-id": id,
      "x-request-id": ID,
    });
    result.body.error.metadata.request_id = ID;
    expect(readManagedRefusal(result).evidence.requestId).toBeNull();
  });
  it.each(["x-agentcc-dispatched", "x-agentcc-request-id", "retry-after"])(
    "rejects duplicate header spelling for %s",
    (name) => {
      const value =
        name === "retry-after"
          ? "9"
          : name === "x-agentcc-dispatched"
            ? "false"
            : ID;
      const refusal = readManagedRefusal(
        wire(429, "managed_spend_velocity", {
          [name]: value,
          [name.toUpperCase()]: value,
        }),
      );
      expect(refusal.evidence).toEqual({
        dispatched: "unknown",
        requestId: null,
      });
      expect(refusal.body).not.toContain("Retry-After:");
    },
  );
  it("keeps nested empty upstream headers ahead of direct/outer headers", () => {
    const result = wire();
    result.headers = {
      "retry-after": "9",
      "x-agentcc-request-id": ID,
      "x-agentcc-dispatched": "false",
    };
    for (const refusal of variants(result)) {
      expect(refusal.evidence).toEqual({
        dispatched: "unknown",
        requestId: null,
      });
      expect(refusal.body).not.toContain("Retry-After:");
    }
  });
  it("accepts native Headers with valid case-insensitive evidence", () => {
    const result = wire(
      429,
      "managed_spend_velocity",
      new Headers({
        "Retry-After": "9",
        "X-AgentCC-Request-ID": ID,
        "X-AgentCC-Dispatched": "true",
      }),
    );
    for (const refusal of variants(result)) {
      expect(refusal.evidence).toEqual({ dispatched: true, requestId: ID });
      expect(refusal.body).toContain("Retry-After: 9.");
    }
  });
});
