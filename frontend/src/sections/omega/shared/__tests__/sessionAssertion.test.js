import { describe, expect, it, vi } from "vitest";

import {
  createAssertionMinter,
  SESSION_EXCHANGE_PATH,
} from "../sessionAssertion";
import { OMEGA_STATE } from "../omegaStates";
import { errorCopy as sandboxErrorCopy } from "../../sandboxes/components/SandboxErrorState";
import { errorCopy as deploymentErrorCopy } from "../../deployments/components/DeploymentErrorState";

// Django's API envelope (tfc/utils/api_errors.py build_error_envelope).
const envelope = (code, message, type = "permission_error") => ({
  status: false,
  type,
  code,
  detail: message,
  message,
  error: message,
  result: message,
});
// What the host axios instance (src/utils/axios.js) rejects with: the body
// spread flat plus statusCode and transportCode. The headers are gone.
const hostError = (statusCode, data = {}) => ({
  ...data,
  statusCode,
  transportCode: statusCode >= 500 ? "ERR_BAD_RESPONSE" : "ERR_BAD_REQUEST",
});
// A plain axios error, for an injected `post` that is not the host instance.
const axiosError = (status, data, headers = {}) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data, headers },
  });

describe("createAssertionMinter", () => {
  it("posts the exact exchange body to the contracted endpoint and returns the assertion", async () => {
    const post = vi.fn(async () => ({
      data: {
        accessToken: "omega_delegate_h.p.s",
        tokenType: "Bearer",
        expiresIn: 45,
      },
    }));
    const mint = createAssertionMinter({ post, now: () => 1_000 });
    await expect(mint()).resolves.toEqual({
      token: "omega_delegate_h.p.s",
      expiresAt: 46_000,
    });
    expect(SESSION_EXCHANGE_PATH).toBe("/accounts/omega/session-exchange/");
    expect(post).toHaveBeenCalledWith("/accounts/omega/session-exchange/", {
      audience: "omega-squad",
    });
  });

  it("mints a new assertion on every call (one-use credentials are never cached)", async () => {
    let n = 0;
    const post = vi.fn(async () => ({
      data: {
        accessToken: `omega_delegate_h.p.${++n}`,
        tokenType: "Bearer",
        expiresIn: 45,
      },
    }));
    const mint = createAssertionMinter({ post });
    const tokens = [(await mint()).token, (await mint()).token];
    expect(tokens).toEqual(["omega_delegate_h.p.1", "omega_delegate_h.p.2"]);
    expect(post).toHaveBeenCalledTimes(2);
  });

  it("maps exchange refusals to session states", async () => {
    const cases = [
      [
        hostError(
          401,
          envelope(
            "not_authenticated",
            "Invalid browser access token",
            "authentication_error",
          ),
        ),
        OMEGA_STATE.SIGNED_OUT,
      ],
      [
        hostError(
          403,
          envelope("permission_denied", "No compatible Omega Squad role"),
        ),
        OMEGA_STATE.REFUSED,
      ],
      [
        hostError(
          503,
          envelope(
            "service_unavailable",
            "Omega delegation is unavailable",
            "service_unavailable",
          ),
        ),
        OMEGA_STATE.UNAVAILABLE,
      ],
      [hostError(500, {}), OMEGA_STATE.UNAVAILABLE],
      // The host instance with no answer at all.
      [
        { message: "Network Error", transportCode: "ERR_NETWORK" },
        OMEGA_STATE.UNAVAILABLE,
      ],
      [new TypeError("Network Error"), OMEGA_STATE.UNAVAILABLE],
      // An injected plain axios post still maps by its response status.
      [axiosError(403, { code: "permission_denied" }), OMEGA_STATE.REFUSED],
      [axiosError(401, {}), OMEGA_STATE.SIGNED_OUT],
    ];
    for (const [error, state] of cases) {
      const mint = createAssertionMinter({
        post: vi.fn(async () => {
          throw error;
        }),
      });
      await expect(mint()).rejects.toMatchObject({ name: "OmegaError", state });
    }
  });

  it("refuses an exchange answer that is not a customer assertion", async () => {
    for (const data of [
      { accessToken: "fi-browser-token", tokenType: "Bearer" },
      { accessToken: "omega_delegate_x", tokenType: "Basic" },
      {},
    ]) {
      const mint = createAssertionMinter({
        post: vi.fn(async () => ({ data })),
      });
      await expect(mint()).rejects.toMatchObject({
        state: OMEGA_STATE.UNAVAILABLE,
        code: "invalid_session_exchange_response",
      });
    }
  });

  it("says the exchange's refusals in Future AGI words, never Django's", async () => {
    const run = async (error) =>
      createAssertionMinter({
        post: vi.fn(async () => {
          throw error;
        }),
      })().catch((caught) => caught);
    const viewer = await run(
      hostError(
        403,
        envelope(
          "omega_viewer_not_admitted",
          "Viewers cannot open Omega. Ask a workspace admin for member access.",
        ),
      ),
    );
    expect(viewer).toMatchObject({
      state: OMEGA_STATE.REFUSED,
      status: 403,
      code: "omega_viewer_not_admitted",
      title: "Viewers can't use Omega",
    });
    const noRole = await run(
      hostError(
        403,
        envelope("permission_denied", "No compatible Omega Squad role"),
      ),
    );
    expect(noRole).toMatchObject({
      state: OMEGA_STATE.REFUSED,
      status: 403,
      code: "session_exchange_refused",
    });
    expect(noRole.message).not.toMatch(/Squad/);
    // Django's 429 today: no seconds in the body, and the host instance drops
    // the Retry-After header, so the copy says "a few seconds".
    const throttled = await run(
      hostError(
        429,
        envelope(
          "throttled",
          "Too many Omega sign-ins in a minute. Wait for the time in Retry-After and try again.",
          "rate_limit_error",
        ),
      ),
    );
    expect(throttled).toMatchObject({
      // The state it had before reason codes (verifier finding 2 on 601bf7b1a).
      state: OMEGA_STATE.UNAVAILABLE,
      status: 429,
      code: "session_exchange_throttled",
      retryAfter: null,
      message:
        "Your account opened Omega too many times in the last minute. Try again in a few seconds.",
    });
    // The wait is shown once the exchange puts it in the body ...
    const timed = await run(
      hostError(429, {
        ...envelope("throttled", "Too many Omega sign-ins in a minute."),
        retryAfterSeconds: 30,
      }),
    );
    expect(timed).toMatchObject({
      code: "session_exchange_throttled",
      retryAfter: 30,
      message:
        "Your account opened Omega too many times in the last minute. Try again in 30 seconds.",
    });
    // ... or when a plain axios post keeps the header.
    const header = await run(
      axiosError(429, envelope("throttled", "x"), { "retry-after": "30" }),
    );
    expect(header.retryAfter).toBe(30);
    const off = await run(
      hostError(
        503,
        envelope(
          "service_unavailable",
          "Omega delegation is unavailable",
          "service_unavailable",
        ),
      ),
    );
    expect(off).toMatchObject({
      state: OMEGA_STATE.UNAVAILABLE,
      code: "session_exchange_unavailable",
    });
    expect(off.message).not.toMatch(/delegation/i);
  });
});

describe("a throttled session exchange in the tabs (regression)", () => {
  it("keeps each tab's approved unavailable heading above the wait", async () => {
    const mint = createAssertionMinter({
      post: vi.fn(async () => {
        throw hostError(429, {
          ...envelope(
            "throttled",
            "Request was throttled.",
            "rate_limit_error",
          ),
          retryAfterSeconds: 12,
        });
      }),
    });
    const error = await mint().catch((caught) => caught);
    for (const copyOf of [sandboxErrorCopy, deploymentErrorCopy]) {
      expect(copyOf(error)).toMatchObject({
        title:
          copyOf === sandboxErrorCopy
            ? "Omega can't complete this request right now."
            : "Omega isn't responding",
        description:
          "Your account opened Omega too many times in the last minute. Try again in 12 seconds.",
        retry: true,
      });
    }
  });

  it("shows a viewer the role refusal in both tabs, not their unavailable headings (regression)", async () => {
    const error = await createAssertionMinter({
      post: vi.fn(async () => {
        throw hostError(
          403,
          envelope("omega_viewer_not_admitted", "Viewers cannot open Omega."),
        );
      }),
    })().catch((caught) => caught);
    for (const copyOf of [sandboxErrorCopy, deploymentErrorCopy]) {
      const copy = copyOf(error);
      expect(copy.title).not.toBe(
        copyOf === sandboxErrorCopy
          ? "Omega can't complete this request right now."
          : "Omega isn't responding",
      );
      expect(copy.retry).toBeFalsy();
      expect(copy.description).toBe(
        "Viewers can't use Omega in this workspace. Ask a workspace owner or admin for member access.",
      );
    }
  });
});
