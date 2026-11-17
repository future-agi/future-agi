import { describe, expect, it } from "vitest";

import contract from "./fixtures/omega-refusal-codes.v1.json";
import {
  REFUSAL_COPY,
  customerCopy,
  formatWait,
  isCustomerSafe,
  isStaleAssertion,
  isWaitRetry,
  refusalEntry,
  retryAfterSeconds,
} from "../omegaReasons";
import { OMEGA_STATE, stateForStatus } from "../omegaStates";

// CP COPY1 / READINESS L07: none of these may reach a customer, in any state.
const COPY1 = [
  /api key/i,
  /omega server/i,
  /operator/i,
  /binding/i,
  /colima/i,
  /container host/i,
  /coming soon/i,
  /\b[A-Z][A-Z0-9]*_[A-Z0-9_]{2,}\b/,
  /omega squad/i,
  /assertion/i,
  /jwks/i,
  /control plane/i,
];
const OPERATOR_TEXT = "Valid Omega API key required.";

const clean = (text) => COPY1.every((pattern) => !pattern.test(text || ""));

// The Django exchange's codes reach the tabs through sessionAssertion.js, by status.
const VIA_EXCHANGE = new Set(["throttled", "delegation_unavailable"]);
// Status-only answers that never reach a page with a code of their own: the
// client builds these requests itself and the copy falls back by status.
const STATUS_ONLY = new Set([
  "bad_request_target",
  "credential_in_query",
  "method_not_allowed",
  "request_body_unreadable",
  "not_found",
]);

// Every role a body's `detail.role` can name, when the code carries one.
const ROLES = ["owner", "admin", "member", "viewer"];

const contractRows = () =>
  Object.entries(contract.sources).flatMap(([source, { codes }]) =>
    Object.entries(codes).flatMap(
      ([code, { statuses, reasons, verbs = [], detail: keys = [] }]) =>
        statuses.flatMap((status) => {
          const details = [null];
          if (keys.includes("role"))
            details.push(...ROLES.map((role) => ({ role })));
          const rows = [null, ...reasons].flatMap((reason) =>
            details.map((detail) => ({ source, code, status, reason, detail })),
          );
          for (const reason of reasons)
            for (const verb of verbs)
              rows.push({ source, code, status, reason, detail: { verb } });
          return rows;
        }),
    ),
  );

// Every variant of a table entry, however it narrows.
const variantsOf = (entry) => [
  entry,
  ...["byReason", "byVerb", "byRole"].flatMap((key) =>
    Object.values(entry[key] || {}).flatMap(variantsOf),
  ),
];

const ADMINS_ONLY = /only workspace owners and admins/i;

describe("refusal contract (unit, contract)", () => {
  it("pins every code EE, the gateway and the exchange send", () => {
    const codes = contractRows().map((row) => row.code);
    for (const code of [
      "assertion_invalid",
      "assertion_expired",
      "assertion_replayed",
      "identity_not_connected",
      "role_out_of_date",
      "verification_unavailable",
      "host_delegation_scope_denied",
      "omega_rate_limited",
      "omega_rate_limit_unavailable",
      "omega_viewer_not_admitted",
    ]) {
      expect(codes).toContain(code);
    }
  });

  it("gives every pinned code Future AGI copy that passes COPY1, in the state its status means", () => {
    const rows = contractRows();
    expect(rows.length).toBeGreaterThan(40);
    for (const { source, code, status, reason, detail } of rows) {
      if (VIA_EXCHANGE.has(code)) continue;
      if (!STATUS_ONLY.has(code)) {
        expect(
          refusalEntry(code, reason, detail),
          `${source} ${code} has no copy`,
        ).not.toBeNull();
      }
      const copy = customerCopy({
        status,
        code,
        reason,
        detail,
        serverMessage: OPERATOR_TEXT,
        fallbackState: stateForStatus(status),
        retryAfter: 7,
      });
      expect(clean(copy.message), `${code}: ${copy.message}`).toBe(true);
      expect(clean(copy.title), `${code}: ${copy.title}`).toBe(true);
      expect(copy.message).not.toContain("{seconds}");
      if (status === 401 || status === 403)
        expect(copy.state).toBe(OMEGA_STATE.REFUSED);
      if (status >= 500) expect(copy.state).toBe(OMEGA_STATE.UNAVAILABLE);
    }
  });

  it("marks exactly EE's retryable credential codes as stale, and 503 pauses as wait", () => {
    expect(isStaleAssertion(401, "assertion_expired", null, null)).toBe(true);
    expect(isStaleAssertion(401, "assertion_replayed", null, null)).toBe(true);
    expect(isStaleAssertion(403, "role_out_of_date", null, null)).toBe(true);
    expect(
      isStaleAssertion(401, "omega_assertion_rejected", "expired_assertion"),
    ).toBe(true);
    expect(isStaleAssertion(401, "assertion_invalid", null, null)).toBe(false);
    expect(isStaleAssertion(403, "identity_not_connected", null, null)).toBe(
      false,
    );
    expect(isStaleAssertion(401, "unauthorized", null, null)).toBe(false);
    // EE's own flag counts for a 401 code this client does not know yet.
    expect(
      isStaleAssertion(401, "assertion_new_kind", null, { retryable: true }),
    ).toBe(true);
    expect(isStaleAssertion(503, "verification_unavailable", null)).toBe(false);
    expect(isWaitRetry(503, "verification_unavailable", null)).toBe(true);
    expect(isWaitRetry(503, "omega_rate_limit_unavailable", null)).toBe(true);
    expect(isWaitRetry(503, "omega_gateway_draining", null)).toBe(true);
    expect(isWaitRetry(503, "omega_issuer_unavailable", null)).toBe(false);
    expect(isWaitRetry(401, "verification_unavailable", null)).toBe(false);
  });

  it("names the not-connected case in its own words", () => {
    const copy = customerCopy({
      status: 403,
      code: "identity_not_connected",
      serverMessage: "This Future AGI workspace is not connected.",
      fallbackState: OMEGA_STATE.REFUSED,
    });
    expect(copy.title).toBe("Omega isn't connected for your account");
    expect(copy.message).toMatch(
      /^Your Future AGI account isn't connected to Omega in this workspace\./,
    );
  });

  it("narrows a route refusal by its reason", () => {
    expect(
      customerCopy({
        status: 403,
        code: "host_delegation_scope_denied",
        reason: "role_below_floor",
        fallbackState: OMEGA_STATE.REFUSED,
      }).title,
    ).toBe("Your role doesn't allow this");
    expect(
      customerCopy({
        status: 403,
        code: "host_delegation_scope_denied",
        reason: "scope_pivot",
        fallbackState: OMEGA_STATE.REFUSED,
      }).title,
    ).toBe("This belongs to another workspace");
    expect(
      customerCopy({
        status: 403,
        code: "host_delegation_scope_denied",
        reason: "something_new",
        fallbackState: OMEGA_STATE.REFUSED,
      }).title,
    ).toBe("Not available from Future AGI");
  });
});

describe("role_forbidden: copy that holds for whoever reads it (unit, regression)", () => {
  // Verifier finding 1 on 601bf7b1a: an admin acting on another member's
  // sandbox read "Only workspace owners and admins can do this".
  const copyFor = (detail, reason = detail?.reason ?? null) =>
    customerCopy({
      status: 403,
      code: "role_forbidden",
      reason,
      detail,
      serverMessage: "Only workspace owners and admins can do this.",
      fallbackState: OMEGA_STATE.REFUSED,
    });

  it("pins creator-only access with its three verbs and the role-table detail", () => {
    const pinned = contract.sources.eeWorkspace.codes.role_forbidden;
    expect(pinned.reasons).toEqual(["not_creator"]);
    expect(pinned.verbs).toEqual(["destroy", "operate", "view"]);
    expect(pinned.detail).toEqual(["reason", "role", "verb"]);
  });

  it("says who may act on a sandbox, per verb, and never 'only owners and admins'", () => {
    const operate = copyFor({ reason: "not_creator", verb: "operate" });
    expect(operate).toMatchObject({
      state: OMEGA_STATE.REFUSED,
      title: "Only the sandbox's creator can do this",
    });
    expect(operate.message).toBe(
      "Only the member who created this sandbox can run commands in it, change its files or change its state. Workspace owners and admins can still open it and delete it.",
    );
    expect(copyFor({ reason: "not_creator", verb: "view" }).message).toBe(
      "Only the member who created this sandbox, and workspace owners and admins, can open it.",
    );
    expect(copyFor({ reason: "not_creator", verb: "destroy" }).message).toBe(
      "Only the member who created this sandbox, or a workspace owner or admin, can delete it.",
    );
    // A verb EE adds later still reads as creator-only, not as a role rule.
    expect(copyFor({ reason: "not_creator", verb: "share" }).message).toBe(
      "Only the member who created this sandbox can do this.",
    );
    for (const row of contractRows().filter(
      (r) => r.reason === "not_creator",
    )) {
      const copy = copyFor(row.detail, row.reason);
      expect(copy.title, JSON.stringify(row)).not.toMatch(ADMINS_ONLY);
      expect(copy.message, JSON.stringify(row)).not.toMatch(ADMINS_ONLY);
    }
  });

  it("names owners and admins only to a member or viewer the role table refused", () => {
    for (const role of ["member", "viewer"]) {
      expect(copyFor({ role }).title).toBe(
        "Only workspace owners and admins can do this",
      );
    }
    for (const detail of [{ role: "owner" }, { role: "admin" }, null]) {
      const copy = copyFor(detail);
      expect(copy.title, JSON.stringify(detail)).toBe("Not allowed");
      expect(copy.message).not.toMatch(ADMINS_ONLY);
    }
    // A prototype key is not a reason, verb or role.
    expect(copyFor({ role: "constructor" }, "toString").title).toBe(
      "Not allowed",
    );
  });

  it("asks a member whose workspace session is missing to reload", () => {
    expect(
      customerCopy({
        status: 403,
        code: "unauthenticated",
        serverMessage: "A current signed-in workspace is required.",
        fallbackState: OMEGA_STATE.REFUSED,
      }),
    ).toMatchObject({
      state: OMEGA_STATE.REFUSED,
      title: "Omega couldn't confirm your sign-in",
    });
  });
});

describe("customerCopy (unit)", () => {
  it("never shows a 401 or 403 server message, known code or not", () => {
    for (const status of [401, 403]) {
      for (const code of [null, "unauthorized", "made_up_code"]) {
        const copy = customerCopy({
          status,
          code,
          serverMessage: "Your request was refused politely.",
          fallbackState: stateForStatus(status),
        });
        expect(copy.message).not.toBe("Your request was refused politely.");
        expect(clean(copy.message)).toBe(true);
      }
    }
  });

  it("keeps a calm domain message and replaces operator text", () => {
    expect(
      customerCopy({
        status: 409,
        code: "version_conflict",
        serverMessage: "Someone changed this deployment. Reload to see it.",
        fallbackState: OMEGA_STATE.ERROR,
      }).message,
    ).toBe("Someone changed this deployment. Reload to see it.");
    const swapped = customerCopy({
      status: 503,
      code: "sandboxes_unavailable",
      serverMessage:
        "Sandboxes need OMEGA_SANDBOX_HOST on the Omega server; ask your operator.",
      fallbackState: OMEGA_STATE.UNAVAILABLE,
    });
    expect(swapped.message).toBe("Sandboxes aren't available right now.");
    const generic = customerCopy({
      status: 500,
      code: "internal",
      serverMessage: "colima socket refused on the container host",
      fallbackState: OMEGA_STATE.ERROR,
    });
    expect(generic.message).toBe("Omega could not complete the request.");
  });

  it("fills the wait from Retry-After", () => {
    const copy = customerCopy({
      status: 429,
      code: "omega_rate_limited",
      reason: "rate_limited",
      fallbackState: OMEGA_STATE.ERROR,
      retryAfter: 12,
    });
    expect(copy.message).toBe(
      "This workspace is sending requests to Omega faster than its limit. Try again in 12 seconds.",
    );
    expect(copy.retry).toBe(true);
    expect(
      customerCopy({
        status: 429,
        code: "omega_rate_limited",
        reason: "exchange_throttled",
        fallbackState: OMEGA_STATE.ERROR,
        retryAfter: 60,
      }).message,
    ).toBe(
      "Your account opened Omega too many times in the last minute. Try again in 1 minute.",
    );
  });

  it("formats waits and reads Retry-After from the header or the gateway body", () => {
    expect(formatWait(1)).toBe("1 second");
    expect(formatWait(2.2)).toBe("3 seconds");
    expect(formatWait(61)).toBe("2 minutes");
    expect(formatWait(null)).toBe("a few seconds");
    expect(retryAfterSeconds("2", null)).toBe(2);
    expect(retryAfterSeconds(null, { retryAfterSeconds: 9 })).toBe(9);
    expect(retryAfterSeconds("soon", {})).toBeNull();
  });

  it("COPY1 check: isCustomerSafe", () => {
    expect(isCustomerSafe("Sandboxes are off in this workspace.")).toBe(true);
    for (const bad of [
      OPERATOR_TEXT,
      "Set OMEGA_HOST_ASSERTIONS on the server",
      "Ask your operator",
      "No binding for this identity",
      "coming soon",
      // Verifier finding 3 on 601bf7b1a: these reached a page unchanged.
      "Assertion audience mismatch for omega-squad",
      "connect ECONNREFUSED 10.0.3.7:7700 (os-daemon)",
      "Omega Squad is not configured",
      "getaddrinfo ENOTFOUND omega-ee",
      "upstream http://omega-ee:7700/workspace failed",
      "proxy to localhost refused",
      "fetch failed at [::1]",
      "dial tcp omega-ee.omega.svc.cluster.local: refused",
      "TypeError: x is undefined\n    at handle (/app/server.js:12:7)",
      "",
      null,
    ]) {
      expect(isCustomerSafe(bad), String(bad)).toBe(false);
    }
    // Calm domain messages, with numbers and times, still pass.
    for (const good of [
      "Someone changed this deployment. Reload to see it.",
      "This workspace already runs 5 sandboxes, its limit.",
      "Snapshots older than 30 days were removed at 10:30.",
      "The command took longer than 900 seconds and was stopped.",
    ]) {
      expect(isCustomerSafe(good), good).toBe(true);
    }
  });

  it("the shipped check refuses everything the tests' COPY1 oracle refuses", () => {
    const samples = [
      "Valid API key required",
      "set it on the Omega server",
      "ask your operator",
      "no binding",
      "colima is down",
      "the container host refused",
      "coming soon",
      "set OMEGA_SANDBOX_HOST",
      "Omega Squad audience",
      "assertion expired",
      "jwks fetch failed",
      "the control plane refused",
    ];
    for (const sample of samples) {
      expect(clean(sample), `oracle misses ${sample}`).toBe(false);
      expect(isCustomerSafe(sample), sample).toBe(false);
    }
  });

  it("has no copy that fails its own check", () => {
    for (const [code, entry] of Object.entries(REFUSAL_COPY)) {
      for (const variant of variantsOf(entry)) {
        const message = variant.message.replace("{seconds}", "5 seconds");
        expect(isCustomerSafe(message), `${code}: ${message}`).toBe(true);
        expect(isCustomerSafe(variant.title), `${code}: ${variant.title}`).toBe(
          true,
        );
        expect(clean(message), `${code}: ${message}`).toBe(true);
      }
    }
  });
});
