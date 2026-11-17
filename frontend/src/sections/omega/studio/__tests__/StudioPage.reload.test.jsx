// A06: a reloaded child has lost its in-memory key. The host never replays
// its spent handoff; reconnecting requires new intake and the child's ready.
import { act, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { hasOmegaConnectToken } from "src/sections/omega/embed";

import {
  ACTIVE_CONNECTION_KEY,
  readSavedConnections,
} from "../saved-connections";
import { GATE_GRACE_MS } from "../use-studio-connection";
import {
  HELLO,
  SERVER,
  TOKEN,
  allStorageText,
  frame,
  fromChild,
  planLink,
  renderStudio,
  resetStudioTestState,
  spyOnFrame,
  storages,
} from "./studio-test-utils";

const READY = { type: "embed.gate", state: "ready" };
const NEEDS_KEY = { type: "embed.gate", state: "needs-key" };
const keys = (posted) => posted.filter((p) => p.message.type === "embed.key");
const passGrace = () =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(GATE_GRACE_MS + 100);
  });
const paste = (token) => {
  fireEvent.change(screen.getByTestId("studio-connect-input"), {
    target: {
      value: `${window.location.origin}/dashboard/studio#connect=${SERVER}&t=${token}`,
    },
  });
  fireEvent.click(screen.getByTestId("studio-connect-submit"));
};

beforeEach(() => {
  resetStudioTestState();
  vi.useFakeTimers({ shouldAdvanceTime: true });
  globalThis.fetch = vi.fn(() => Promise.reject(new TypeError("offline")));
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  resetStudioTestState();
});

describe("Studio memory-only child reload contract", () => {
  it.each(["link", "paste"])(
    "requires a fresh %s after reload, with one exact-origin handoff and no stored key",
    async (source) => {
      if (source === "link") planLink();
      renderStudio();
      if (source === "paste") paste(TOKEN);
      const initial = frame();
      const posted = spyOnFrame(initial);
      await fromChild(initial, HELLO);
      await fromChild(initial, READY);
      expect(keys(posted)).toEqual([
        { message: { type: "embed.key", token: TOKEN }, targetOrigin: SERVER },
      ]);
      expect(hasOmegaConnectToken()).toBe(false);
      expect(screen.getByTestId("studio-status")).toHaveAttribute(
        "data-state",
        "connected",
      );

      // Simulate the reloaded document's hello + gate. Same WindowProxy,
      // no child memory; the host must not resurrect its original token.
      await fromChild(initial, HELLO);
      await fromChild(initial, NEEDS_KEY);
      await fromChild(initial, HELLO);
      expect(keys(posted)).toHaveLength(1);
      expect(screen.getByTestId("studio-status")).toHaveAttribute(
        "data-state",
        "needs-key",
      );
      await passGrace();
      expect(screen.getByTestId("studio-connect-notice")).toHaveAttribute(
        "data-kind",
        "needs-key",
      );
      expect(screen.queryByTestId("omega-embed-frame")).toBeNull();
      expect(storages()[1].getItem(ACTIVE_CONNECTION_KEY)).toBeNull();
      expect(readSavedConnections()).toHaveLength(1);

      // A saved URL is not fresh intake, even after a successful first visit.
      fireEvent.click(screen.getByTestId("studio-saved-connect"));
      const saved = frame();
      const savedPosted = spyOnFrame(saved);
      await fromChild(saved, HELLO);
      await fromChild(saved, NEEDS_KEY);
      await passGrace();
      expect(keys(savedPosted)).toEqual([]);

      // The host does not authenticate link text; the server's gate does.
      // This test's fresh valid fixture key receives READY, not REFUSED.
      const freshToken = "fresh-studio-fixture-".padEnd(43, "1");
      if (source === "link") {
        act(() => {
          planLink(SERVER, freshToken);
          window.dispatchEvent(new Event("omega:connect"));
        });
      } else {
        paste(freshToken);
      }
      const fresh = frame();
      const freshPosted = spyOnFrame(fresh);
      await fromChild(fresh, HELLO, "http://127.0.0.1:9999");
      act(() =>
        window.dispatchEvent(
          new MessageEvent("message", {
            data: HELLO,
            origin: SERVER,
            source: window,
          }),
        ),
      );
      expect(freshPosted).toEqual([]);
      await fromChild(fresh, HELLO);
      expect(keys(freshPosted)).toEqual([
        {
          message: { type: "embed.key", token: freshToken },
          targetOrigin: SERVER,
        },
      ]);
      expect(screen.getByTestId("studio-status")).toHaveAttribute(
        "data-state",
        "checking",
      );
      await fromChild(fresh, READY);
      await fromChild(fresh, HELLO);
      expect(keys(freshPosted)).toHaveLength(1);
      expect(screen.getByTestId("studio-status")).toHaveAttribute(
        "data-state",
        "connected",
      );
      expect(allStorageText()).not.toContain(TOKEN);
      expect(allStorageText()).not.toContain(freshToken);
      expect(window.location.href).not.toContain(freshToken);
      expect(fresh.getAttribute("src")).not.toContain(freshToken);
    },
  );

  it("accepts fresh intake during the needs-key grace without the old timer disconnecting it", async () => {
    planLink();
    renderStudio();
    const initial = frame();
    const posted = spyOnFrame(initial);
    await fromChild(initial, HELLO);
    await fromChild(initial, READY);
    await fromChild(initial, HELLO);
    await fromChild(initial, NEEDS_KEY);
    act(() => {
      planLink(SERVER, TOKEN); // freshly opened link, not automatic replay
      window.dispatchEvent(new Event("omega:connect"));
    });
    const fresh = frame();
    expect(fresh).not.toBe(initial);
    const freshPosted = spyOnFrame(fresh);
    await fromChild(fresh, HELLO);
    await fromChild(fresh, READY);
    await passGrace();
    expect(keys(posted)).toHaveLength(1);
    expect(keys(freshPosted)).toEqual([
      { message: { type: "embed.key", token: TOKEN }, targetOrigin: SERVER },
    ]);
    expect(screen.getByTestId("studio-status")).toHaveAttribute(
      "data-state",
      "connected",
    );
    expect(allStorageText()).not.toContain(TOKEN);
  });
});
