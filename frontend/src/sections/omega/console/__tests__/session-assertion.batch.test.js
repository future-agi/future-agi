// CP P7 on the host: one exchange call mints a hello's whole batch, and the
// viewer's refusal is its own state (CP P10). The request and answer shapes
// are the session exchange's (futureagi/accounts/views/omega_session.py and
// serializers/omega_session.py): `count` 1-8, `accessTokens` every key with
// `accessToken` the first.
import { describe, expect, it, vi } from "vitest";

import {
  MAX_KEYS_PER_MINT,
  MINT_STATE,
  OMEGA_AUDIENCE,
  SESSION_EXCHANGE_PATH,
  VIEWER_REFUSAL_CODE,
  createAssertionMinter,
} from "../session-assertion";

const key = (n) => `omega_delegate_eyJ${n}.payload.sig`;
const batch = (n) => ({
  tokenType: "Bearer",
  accessToken: key(0),
  accessTokens: Array.from({ length: n }, (_, i) => key(i)),
  expiresIn: 45,
});
const answering = (data) => vi.fn(async () => ({ data }));
const rejecting = (statusCode, body = {}) =>
  vi.fn(async () => {
    throw { ...body, statusCode };
  });
const failureOf = async (promise) => {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error("expected the mint to fail");
};

describe("batched session exchange", () => {
  it("sends count only for more than one key, at most 8", async () => {
    const post = answering(batch(8));
    const mint = createAssertionMinter({ post });
    await mint();
    await mint({ count: 1 });
    await mint({ count: 4 });
    await mint({ count: 20 });
    expect(post.mock.calls.map(([, body]) => body)).toEqual([
      { audience: OMEGA_AUDIENCE },
      { audience: OMEGA_AUDIENCE },
      { audience: OMEGA_AUDIENCE, count: 4 },
      { audience: OMEGA_AUDIENCE, count: MAX_KEYS_PER_MINT },
    ]);
    expect(
      post.mock.calls.every(([path]) => path === SESSION_EXCHANGE_PATH),
    ).toBe(true);
  });

  it("returns every key of the answer, never more than asked", async () => {
    const mint = createAssertionMinter({
      post: answering(batch(4)),
      now: () => 0,
    });
    await expect(mint({ count: 4 })).resolves.toEqual({
      token: key(0),
      tokens: [key(0), key(1), key(2), key(3)],
      expiresAt: 45_000,
    });
    const capped = createAssertionMinter({ post: answering(batch(6)) });
    expect((await capped({ count: 3 })).tokens).toHaveLength(3);
  });

  it("uses the one key of a backend that ignores count", async () => {
    const mint = createAssertionMinter({
      post: answering({
        tokenType: "Bearer",
        accessToken: key(0),
        expiresIn: 45,
      }),
    });
    expect((await mint({ count: 4 })).tokens).toEqual([key(0)]);
  });

  it.each([
    ["an empty list", { ...batch(2), accessTokens: [] }],
    ["a list that is not one", { ...batch(2), accessTokens: key(1) }],
    [
      "a key that is not an assertion",
      { ...batch(2), accessTokens: [key(0), "eyJx.y.z"] },
    ],
    [
      "an oversize key",
      {
        ...batch(2),
        accessTokens: [key(0), `omega_delegate_${"x".repeat(9000)}`],
      },
    ],
  ])("refuses an answer with %s", async (_, data) => {
    const error = await failureOf(
      createAssertionMinter({ post: answering(data) })({ count: 2 }),
    );
    expect(error).toMatchObject({
      state: MINT_STATE.UNAVAILABLE,
      code: "invalid_session_exchange_response",
    });
  });
});

describe("exchange refusals", () => {
  // Django's envelope (tfc/utils/api_errors.py) through the host axios instance.
  const envelope = (code, message) => ({
    status: false,
    code,
    detail: message,
    message,
    error: message,
    result: message,
  });

  it("reads the viewer's refusal as its own state", async () => {
    const error = await failureOf(
      createAssertionMinter({
        post: rejecting(
          403,
          envelope(
            VIEWER_REFUSAL_CODE,
            "Viewers cannot open Omega. Ask a workspace admin for member access.",
          ),
        ),
      })({ count: 4 }),
    );
    expect(error).toMatchObject({
      state: MINT_STATE.VIEWER,
      status: 403,
      code: VIEWER_REFUSAL_CODE,
    });
  });

  it("keeps other 403s refused, 401 signed out, and 429 unavailable with its wait", async () => {
    const refused = await failureOf(
      createAssertionMinter({
        post: rejecting(
          403,
          envelope("permission_denied", "No compatible Omega Squad role"),
        ),
      })(),
    );
    expect(refused.state).toBe(MINT_STATE.REFUSED);
    const out = await failureOf(
      createAssertionMinter({ post: rejecting(401) })(),
    );
    expect(out.state).toBe(MINT_STATE.SIGNED_OUT);
    const throttled = await failureOf(
      createAssertionMinter({
        post: rejecting(429, {
          ...envelope("throttled", "Too many"),
          retryAfterSeconds: 12,
        }),
      })(),
    );
    expect(throttled).toMatchObject({
      state: MINT_STATE.UNAVAILABLE,
      status: 429,
      retryAfter: 12,
    });
    const noWait = await failureOf(
      createAssertionMinter({
        post: rejecting(429, envelope("throttled", "x")),
      })(),
    );
    expect(noWait.retryAfter).toBeNull();
  });
});
