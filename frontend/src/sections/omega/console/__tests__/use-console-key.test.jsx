import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { EMBED_STATUS } from "src/sections/omega/embed";

import { AssertionMintError, MINT_STATE } from "../session-assertion";
import { useConsoleKey } from "../use-console-key";

const TOKEN = "omega_delegate_eyJ.console.sig";

const refusedMint = () =>
  vi.fn(async () => {
    throw new AssertionMintError({ state: MINT_STATE.REFUSED, status: 403 });
  });

// Drives the hook the way EmbeddedSurface does: getToken once per hello, and
// onStatusChange with each status the surface commits.
const setup = (mintAssertion) => {
  const view = renderHook(() => useConsoleKey({ mintAssertion }));
  const hello = () =>
    act(async () => {
      await view.result.current.getToken();
    });
  const report = (status) =>
    act(() => {
      view.result.current.onStatusChange(status);
    });
  return { view, hello, report };
};

describe("useConsoleKey", () => {
  it("mints a fresh key for every hello and keeps none", async () => {
    const mintAssertion = vi.fn(async () => ({ token: TOKEN }));
    const { view } = setup(mintAssertion);

    await expect(view.result.current.getToken()).resolves.toBe(TOKEN);
    await expect(view.result.current.getToken()).resolves.toBe(TOKEN);
    expect(mintAssertion).toHaveBeenCalledTimes(2);
    expect(view.result.current.mintFailure).toBeNull();
  });

  it("keeps a failed mint until Reconnect, so repeated hellos do not ask again", async () => {
    const mintAssertion = refusedMint();
    const { view, hello, report } = setup(mintAssertion);

    report(EMBED_STATUS.connecting);
    await hello();
    report(EMBED_STATUS.needsKey);
    await hello();
    await hello();
    expect(mintAssertion).toHaveBeenCalledTimes(1);
    expect(view.result.current.mintFailure).toBe(MINT_STATE.REFUSED);

    // Reconnect: the surface leaves the panel for connecting.
    report(EMBED_STATUS.connecting);
    expect(view.result.current.mintFailure).toBeNull();
    mintAssertion.mockImplementation(async () => ({ token: TOKEN }));
    await expect(view.result.current.getToken()).resolves.toBe(TOKEN);
    expect(mintAssertion).toHaveBeenCalledTimes(2);
  });

  it("does not take a late hello's return to connecting for a Reconnect", async () => {
    const mintAssertion = refusedMint();
    const { view, hello, report } = setup(mintAssertion);

    report(EMBED_STATUS.connecting);
    report(EMBED_STATUS.diagnosing);
    report(EMBED_STATUS.timeout);
    // The Console answers after all; its mint is refused, and the surface
    // goes from the no-answer panel back to connecting by itself.
    await hello();
    report(EMBED_STATUS.connecting);

    expect(view.result.current.mintFailure).toBe(MINT_STATE.REFUSED);
    await hello();
    expect(mintAssertion).toHaveBeenCalledTimes(1);

    // The same goes for a hello that lands while the cause is being looked up.
    const second = setup(refusedMint());
    second.report(EMBED_STATUS.connecting);
    second.report(EMBED_STATUS.diagnosing);
    await second.hello();
    second.report(EMBED_STATUS.connecting);
    expect(second.view.result.current.mintFailure).toBe(MINT_STATE.REFUSED);
  });

  it("does not take the same status reported again for a Reconnect", async () => {
    const mintAssertion = refusedMint();
    const { view, hello, report } = setup(mintAssertion);

    report(EMBED_STATUS.timeout);
    await hello();
    report(EMBED_STATUS.connecting);
    // EmbeddedSurface reports again when only the diagnosis changes.
    report(EMBED_STATUS.connecting);

    expect(view.result.current.mintFailure).toBe(MINT_STATE.REFUSED);
    await hello();
    expect(mintAssertion).toHaveBeenCalledTimes(1);
  });

  it("files an unknown mint error as 'unavailable'", async () => {
    const { view, hello } = setup(
      vi.fn(async () => {
        throw new Error("network");
      }),
    );
    await hello();
    expect(view.result.current.mintFailure).toBe(MINT_STATE.UNAVAILABLE);
  });
});
