import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OMEGA_STATE, OmegaError } from "../omegaStates";

const workspace = { currentWorkspaceId: "ws-1" };
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspace,
}));

const { probeOmegaSession, useOmegaSession } = await import(
  "../useOmegaSession"
);

const gatewayUp = vi.fn(
  async () => new Response('{"ready":true}', { status: 200 }),
);

describe("probeOmegaSession", () => {
  it("is ready only when an assertion is minted and the gateway is live", async () => {
    const mintAssertion = vi.fn(async () => ({
      token: "omega_delegate_a.b.c",
    }));
    await expect(
      probeOmegaSession({
        mintAssertion,
        fetchImpl: gatewayUp,
        basePath: "/omega/api",
      }),
    ).resolves.toEqual({ state: OMEGA_STATE.READY, code: null, message: null });
    expect(gatewayUp).toHaveBeenCalledWith(
      "/omega/api/readyz",
      expect.objectContaining({ credentials: "omit" }),
    );
    const gatewayDown = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    await expect(
      probeOmegaSession({ mintAssertion, fetchImpl: gatewayDown }),
    ).resolves.toMatchObject({
      state: OMEGA_STATE.UNAVAILABLE,
      code: "gateway_unavailable",
    });
  });

  it("reports viewers and disabled delegation without claiming readiness", async () => {
    for (const state of [
      OMEGA_STATE.REFUSED,
      OMEGA_STATE.UNAVAILABLE,
      OMEGA_STATE.SIGNED_OUT,
    ]) {
      const mintAssertion = vi.fn(async () => {
        throw new OmegaError({ state, status: 403, code: "c" });
      });
      await expect(
        probeOmegaSession({ mintAssertion, fetchImpl: gatewayUp }),
      ).resolves.toMatchObject({ state, code: "c" });
    }
  });

  it("passes the exchange's Future AGI title and wait through to the gate", async () => {
    const mintAssertion = vi.fn(async () => {
      throw new OmegaError({
        state: OMEGA_STATE.UNAVAILABLE,
        status: 429,
        code: "session_exchange_throttled",
        title: "Too many sign-ins",
        message: "Try again in 30 seconds.",
        retryAfter: 30,
      });
    });
    await expect(
      probeOmegaSession({ mintAssertion, fetchImpl: gatewayUp }),
    ).resolves.toEqual({
      state: OMEGA_STATE.UNAVAILABLE,
      code: "session_exchange_throttled",
      title: "Too many sign-ins",
      message: "Try again in 30 seconds.",
      retryAfter: 30,
    });
  });
});

describe("useOmegaSession", () => {
  beforeEach(() => {
    workspace.currentWorkspaceId = "ws-1";
  });

  it("starts as checking, settles on the probe result, and re-probes on workspace switch", async () => {
    const results = [
      { state: OMEGA_STATE.READY, code: null, message: null },
      { state: OMEGA_STATE.REFUSED, code: "viewer", message: "no" },
    ];
    const probe = vi.fn(async () => results.shift());
    const { result, rerender } = renderHook(() => useOmegaSession({ probe }));
    expect(result.current.state).toBe("checking");
    await waitFor(() => expect(result.current.state).toBe(OMEGA_STATE.READY));
    expect(result.current.workspaceId).toBe("ws-1");
    workspace.currentWorkspaceId = "ws-2";
    rerender();
    await waitFor(() => expect(result.current.state).toBe(OMEGA_STATE.REFUSED));
    expect(result.current).toMatchObject({
      code: "viewer",
      workspaceId: "ws-2",
    });
    expect(probe).toHaveBeenCalledTimes(2);
  });

  it("ignores a slow probe for a workspace that is no longer selected", async () => {
    let releaseFirst;
    const first = new Promise((resolve) => {
      releaseFirst = resolve;
    });
    const probe = vi
      .fn()
      .mockImplementationOnce(() => first)
      .mockImplementationOnce(async () => ({
        state: OMEGA_STATE.UNAVAILABLE,
        code: "gateway_unavailable",
        message: "down",
      }));
    const { result, rerender } = renderHook(() => useOmegaSession({ probe }));
    workspace.currentWorkspaceId = "ws-2";
    rerender();
    await waitFor(() =>
      expect(result.current.state).toBe(OMEGA_STATE.UNAVAILABLE),
    );
    await act(async () => {
      releaseFirst({ state: OMEGA_STATE.READY, code: null, message: null });
    });
    expect(result.current.state).toBe(OMEGA_STATE.UNAVAILABLE);
  });
});
