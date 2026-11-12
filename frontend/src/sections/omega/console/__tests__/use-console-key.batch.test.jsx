// The key hook with batches (CP P7) and with a backend that fails while the
// Console is open (RAS F1: recovers without a reload).
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { EMBED_STATUS } from "src/sections/omega/embed";

import { AssertionMintError, MINT_STATE } from "../session-assertion";
import { MINT_BACKOFF_MS, useConsoleKey } from "../use-console-key";

const keys = (n) => Array.from({ length: n }, (_, i) => `omega_delegate_k${i}`);

const setup = (mintAssertion) => {
  let clock = 1_000_000;
  const view = renderHook(() =>
    useConsoleKey({ mintAssertion, now: () => clock }),
  );
  const tokens = (want) => act(async () => view.result.current.getTokens(want));
  const advance = (ms) => {
    clock += ms;
  };
  const report = (status) =>
    act(() => view.result.current.onStatusChange(status));
  return { view, tokens, advance, report };
};

const failing = (state, extra = {}) =>
  new AssertionMintError({ state, status: 503, ...extra });

describe("useConsoleKey batches", () => {
  it("mints a hello's whole batch in one call", async () => {
    const mintAssertion = vi.fn(async ({ count }) => ({
      token: keys(count)[0],
      tokens: keys(count),
    }));
    const { tokens } = setup(mintAssertion);
    await expect(tokens(4)).resolves.toEqual(keys(4));
    expect(mintAssertion).toHaveBeenCalledTimes(1);
    expect(mintAssertion).toHaveBeenCalledWith({ count: 4 });
  });

  it("gives getToken one key from a batch-capable minter", async () => {
    const mintAssertion = vi.fn(async ({ count }) => ({ tokens: keys(count) }));
    const { view } = setup(mintAssertion);
    await expect(view.result.current.getToken()).resolves.toBe(keys(1)[0]);
    expect(mintAssertion).toHaveBeenCalledWith({ count: 1 });
  });
});

describe("useConsoleKey document cancellation", () => {
  it.each([MINT_STATE.SIGNED_OUT, MINT_STATE.REFUSED, MINT_STATE.UNAVAILABLE])(
    "retains the new document's %s if its connecting status arrives after the mint",
    async (state) => {
      const mint = vi
        .fn()
        .mockResolvedValueOnce({ tokens: keys(1) })
        .mockRejectedValue(failing(state));
      const { view, report } = setup(mint);
      const a = new AbortController();
      const b = new AbortController();
      await act(async () => {
        await view.result.current.getTokens(1, { signal: a.signal });
      });
      report(EMBED_STATUS.connected);
      a.abort();
      await act(async () => {
        await view.result.current.getTokens(1, { signal: b.signal });
      });
      expect(view.result.current.mintFailure).toBe(state);
      // React can batch B's immediate mint failure with its document reset.
      // This notification belongs to B, not a later user-requested reconnect.
      report(EMBED_STATUS.connecting);
      expect(view.result.current.mintFailure).toBe(state);
      await act(async () => {
        expect(
          await view.result.current.getTokens(1, { signal: b.signal }),
        ).toEqual([]);
      });
      expect(mint).toHaveBeenCalledTimes(2);
      b.abort();
      mint.mockResolvedValue({ tokens: keys(1) });
      const c = new AbortController();
      await act(async () => {
        expect(
          await view.result.current.getTokens(1, { signal: c.signal }),
        ).toEqual(keys(1));
      });
      expect(view.result.current.mintFailure).toBeNull();
      expect(mint).toHaveBeenCalledTimes(3);
    },
  );

  it("ignores an aborted failure even before the replacement starts minting", async () => {
    let reject;
    const mint = vi.fn(
      () =>
        new Promise((resolve, fail) => {
          reject = fail;
        }),
    );
    const { view } = setup(mint);
    const controller = new AbortController();
    const pending = view.result.current.getTokens(2, {
      signal: controller.signal,
    });
    controller.abort();
    await act(async () => {
      reject(failing(MINT_STATE.VIEWER));
      expect(await pending).toEqual([]);
    });
    expect(view.result.current.mintFailure).toBeNull();
  });

  it("does not let obsolete success clear a replacement's held failure", async () => {
    let finishOld;
    const mint = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishOld = resolve;
          }),
      )
      .mockRejectedValue(failing(MINT_STATE.REFUSED));
    const { view } = setup(mint);
    const oldDocument = new AbortController();
    const nextDocument = new AbortController();
    const pending = view.result.current.getTokens(1, {
      signal: oldDocument.signal,
    });
    oldDocument.abort();
    await act(async () => {
      await view.result.current.getTokens(1, { signal: nextDocument.signal });
    });
    expect(view.result.current.mintFailure).toBe(MINT_STATE.REFUSED);
    await act(async () => {
      finishOld({ tokens: keys(1) });
      expect(await pending).toEqual([]);
    });
    expect(view.result.current.mintFailure).toBe(MINT_STATE.REFUSED);
    await act(async () => {
      expect(
        await view.result.current.getTokens(1, { signal: nextDocument.signal }),
      ).toEqual([]);
    });
    expect(mint).toHaveBeenCalledTimes(2);
  });

  it("ignores an already-aborted request and starts a fresh document without the old refusal", async () => {
    const mint = vi
      .fn()
      .mockRejectedValueOnce(failing(MINT_STATE.REFUSED))
      .mockResolvedValue({ tokens: keys(1) });
    const { view } = setup(mint);
    const oldDocument = new AbortController();
    await act(async () => {
      await view.result.current.getToken({ signal: oldDocument.signal });
    });
    oldDocument.abort();
    await act(async () => {
      expect(
        await view.result.current.getToken({ signal: oldDocument.signal }),
      ).toBeNull();
    });
    expect(mint).toHaveBeenCalledTimes(1);
    const nextDocument = new AbortController();
    await act(async () => {
      expect(
        await view.result.current.getToken({ signal: nextDocument.signal }),
      ).toBe(keys(1)[0]);
    });
    expect(view.result.current.mintFailure).toBeNull();
    expect(mint).toHaveBeenCalledTimes(2);
  });
});

describe("useConsoleKey after a failed mint", () => {
  it("retries an unavailable backend after a doubling backoff, then heals", async () => {
    const mintAssertion = vi.fn(async () => {
      throw failing(MINT_STATE.UNAVAILABLE);
    });
    const { view, tokens, advance } = setup(mintAssertion);

    expect(await tokens(4)).toEqual([]);
    expect(view.result.current.mintFailure).toBe(MINT_STATE.UNAVAILABLE);
    // Hellos inside the backoff ask nothing.
    advance(MINT_BACKOFF_MS[0] - 1);
    await tokens(4);
    expect(mintAssertion).toHaveBeenCalledTimes(1);
    // After it, one more try; it fails again and the wait doubles.
    advance(1);
    await tokens(4);
    expect(mintAssertion).toHaveBeenCalledTimes(2);
    advance(MINT_BACKOFF_MS[1] - 1);
    await tokens(4);
    expect(mintAssertion).toHaveBeenCalledTimes(2);

    // The backend is back: the next try after the wait succeeds and clears it.
    mintAssertion.mockImplementation(async () => ({ tokens: keys(4) }));
    advance(1);
    expect(await tokens(4)).toEqual(keys(4));
    expect(view.result.current.mintFailure).toBeNull();
  });

  it("caps the backoff at 30 s and honours a longer Retry-After", async () => {
    const mintAssertion = vi.fn(async () => {
      throw failing(MINT_STATE.UNAVAILABLE);
    });
    const { tokens, advance } = setup(mintAssertion);
    for (const wait of MINT_BACKOFF_MS) {
      await tokens(2);
      advance(wait);
    }
    const before = mintAssertion.mock.calls.length;
    await tokens(2);
    advance(MINT_BACKOFF_MS.at(-1) - 1);
    await tokens(2);
    expect(mintAssertion.mock.calls.length).toBe(before + 1);

    const throttled = setup(
      vi.fn(async () => {
        throw failing(MINT_STATE.UNAVAILABLE, { status: 429, retryAfter: 20 });
      }),
    );
    await throttled.tokens(2);
    throttled.advance(19_999);
    await throttled.tokens(2);
    expect(throttled.view.result.current.mintFailure).toBe(
      MINT_STATE.UNAVAILABLE,
    );
  });

  it.each([MINT_STATE.SIGNED_OUT, MINT_STATE.REFUSED, MINT_STATE.VIEWER])(
    "keeps %s until Reconnect, however long the hellos go on",
    async (state) => {
      const mintAssertion = vi.fn(async () => {
        throw failing(state);
      });
      const { view, tokens, advance, report } = setup(mintAssertion);
      report(EMBED_STATUS.connecting);
      await tokens(4);
      advance(10 * 60 * 1000);
      await tokens(4);
      expect(mintAssertion).toHaveBeenCalledTimes(1);
      expect(view.result.current.mintFailure).toBe(state);

      report(EMBED_STATUS.needsKey);
      report(EMBED_STATUS.connecting);
      mintAssertion.mockImplementation(async () => ({ tokens: keys(1) }));
      expect(await tokens(1)).toEqual(keys(1));
      expect(view.result.current.mintFailure).toBeNull();
    },
  );
});
