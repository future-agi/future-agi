import { describe, expect, it, vi } from "vitest";

import {
  AssertionMintError,
  MINT_STATE,
  OMEGA_AUDIENCE,
  SESSION_EXCHANGE_PATH,
  createAssertionMinter,
} from "../session-assertion";

const ok = (data) => vi.fn(async () => ({ data }));
const GOOD = {
  tokenType: "Bearer",
  accessToken: "omega_delegate_abc.def.ghi",
  expiresIn: 45,
};

// The host axios instance rejects with the response body plus statusCode.
const hostRejection = (statusCode, body = {}) =>
  vi.fn(async () => {
    throw { ...body, statusCode };
  });

const failureOf = async (mint) => {
  try {
    await mint();
  } catch (error) {
    return error;
  }
  throw new Error("expected the mint to fail");
};

describe("createAssertionMinter", () => {
  it("asks Future AGI's backend for an omega-squad assertion and returns it", async () => {
    const post = ok(GOOD);
    const mint = createAssertionMinter({ post, now: () => 1_000 });

    await expect(mint()).resolves.toEqual({
      token: GOOD.accessToken,
      tokens: [GOOD.accessToken],
      expiresAt: 46_000,
    });
    expect(post).toHaveBeenCalledWith(SESSION_EXCHANGE_PATH, {
      audience: OMEGA_AUDIENCE,
    });
    expect(SESSION_EXCHANGE_PATH).toBe("/accounts/omega/session-exchange/");
    expect(OMEGA_AUDIENCE).toBe("omega-squad");
  });

  it("mints a new assertion on every call", async () => {
    const post = ok(GOOD);
    const mint = createAssertionMinter({ post });
    await mint();
    await mint();
    expect(post).toHaveBeenCalledTimes(2);
  });

  it.each([
    ["no token", { tokenType: "Bearer" }],
    ["wrong type", { ...GOOD, tokenType: "Basic" }],
    ["not an Omega assertion", { ...GOOD, accessToken: "eyJhbGciOi.x.y" }],
  ])("refuses a malformed exchange response (%s)", async (_, data) => {
    const error = await failureOf(createAssertionMinter({ post: ok(data) }));
    expect(error).toBeInstanceOf(AssertionMintError);
    expect(error.state).toBe(MINT_STATE.UNAVAILABLE);
    expect(error.code).toBe("invalid_session_exchange_response");
  });

  it.each([
    [401, MINT_STATE.SIGNED_OUT],
    [403, MINT_STATE.REFUSED],
    [503, MINT_STATE.UNAVAILABLE],
    [500, MINT_STATE.UNAVAILABLE],
  ])("maps a %s from the host client to %s", async (status, state) => {
    const post = hostRejection(status, { code: "omega_role_refused" });
    const error = await failureOf(createAssertionMinter({ post }));
    expect(error.state).toBe(state);
    expect(error.status).toBe(status);
    expect(error.code).toBe("omega_role_refused");
  });

  it("reads a plain axios error too", async () => {
    const post = vi.fn(async () => {
      throw Object.assign(new Error("HTTP 403"), {
        response: { status: 403, data: { error: "viewer_refused" } },
      });
    });
    const error = await failureOf(createAssertionMinter({ post }));
    expect(error.state).toBe(MINT_STATE.REFUSED);
    expect(error.code).toBe("viewer_refused");
  });

  it("treats a network failure as unavailable", async () => {
    const post = vi.fn(async () => {
      throw { message: "Network Error" };
    });
    const error = await failureOf(createAssertionMinter({ post }));
    expect(error.state).toBe(MINT_STATE.UNAVAILABLE);
    expect(error.status).toBeNull();
  });
});
