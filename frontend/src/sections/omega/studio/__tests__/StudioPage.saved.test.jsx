// Saved/restored connections hold URL and label only. RC1's framed child
// starts without a key and needs a freshly opened/pasted link on every mount.
import { act, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NO_KEY_GATE_TIMEOUT_MS } from "src/sections/omega/embed";

import {
  ACTIVE_CONNECTION_KEY,
  SAVED_CONNECTIONS_KEY,
  readSavedConnections,
} from "../saved-connections";
import { GATE_GRACE_MS } from "../use-studio-connection";
import {
  HELLO,
  SERVER,
  frame,
  fromChild,
  planLink,
  renderStudio,
  resetStudioTestState,
  spyOnFrame,
  storages,
} from "./studio-test-utils";

const [local, session] = [() => storages()[0], () => storages()[1]];
const seed = (list) =>
  local().setItem(SAVED_CONNECTIONS_KEY, JSON.stringify(list));
const passGrace = () =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(GATE_GRACE_MS + 100);
  });

beforeEach(() => {
  resetStudioTestState();
  globalThis.fetch = vi.fn(() => Promise.reject(new TypeError("offline")));
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  resetStudioTestState();
});

describe("Studio saved connections", () => {
  it("lists URL and label only, renames and removes", () => {
    seed([
      { url: SERVER, label: "Support agent", token: "should-not-survive" },
      { url: "https://evil.example", label: "bad" },
    ]);
    renderStudio();
    expect(screen.getAllByTestId("studio-saved-row")).toHaveLength(1);
    expect(screen.getByTestId("studio-saved-label")).toHaveTextContent(
      "Support agent",
    );
    fireEvent.click(screen.getByLabelText("Rename Support agent"));
    fireEvent.change(screen.getByTestId("studio-saved-label-input"), {
      target: { value: "Triage" },
    });
    fireEvent.submit(screen.getByTestId("studio-saved-label-input"));
    expect(JSON.parse(local().getItem(SAVED_CONNECTIONS_KEY))).toEqual([
      { url: SERVER, label: "Triage" },
    ]);
    fireEvent.click(screen.getByTestId("studio-saved-remove"));
    expect(screen.queryByTestId("studio-saved")).toBeNull();
    expect(JSON.parse(local().getItem(SAVED_CONNECTIONS_KEY))).toEqual([]);
  });

  it.each(["saved", "restored", "keyless paste"])(
    "%s never recovers a key and returns to the fresh-link screen",
    async (source) => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      seed([{ url: SERVER, label: "Mine", token: "stale-storage-token" }]);
      if (source === "restored")
        session().setItem(ACTIVE_CONNECTION_KEY, SERVER);
      renderStudio({ path: "/dashboard/studio/t/th_1" });
      if (source === "saved") {
        fireEvent.click(screen.getByTestId("studio-saved-connect"));
      } else if (source === "keyless paste") {
        fireEvent.change(screen.getByTestId("studio-connect-input"), {
          target: { value: "127.0.0.1:4191" },
        });
        fireEvent.click(screen.getByTestId("studio-connect-submit"));
      }
      const element = frame();
      expect(element).toHaveAttribute("src", `${SERVER}/?embed=1#/t/th_1`);
      const posted = spyOnFrame(element);
      await fromChild(element, HELLO);
      await fromChild(element, HELLO);
      expect(posted.some((p) => p.message.type === "embed.key")).toBe(false);
      expect(screen.getByTestId("studio-status")).toHaveAttribute(
        "data-state",
        "connecting",
      );
      await fromChild(element, { type: "embed.gate", state: "needs-key" });
      expect(screen.getByTestId("omega-embed-gate")).toHaveTextContent(
        "Local Studio needs its key",
      );
      await passGrace();
      expect(screen.getByTestId("studio-connect-notice")).toHaveAttribute(
        "data-kind",
        "needs-key",
      );
      expect(screen.queryByTestId("omega-embed-frame")).toBeNull();
      expect(readSavedConnections()).toEqual([{ url: SERVER, label: "Mine" }]);
      expect(session().getItem(ACTIVE_CONNECTION_KEY)).toBeNull();
    },
  );

  it("asks for a new link when a keyless child never reports its gate", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    seed([{ url: SERVER, label: "Mine" }]);
    renderStudio();
    fireEvent.click(screen.getByTestId("studio-saved-connect"));
    const element = frame();
    const posted = spyOnFrame(element);
    await fromChild(element, HELLO);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NO_KEY_GATE_TIMEOUT_MS - 500);
    });
    expect(screen.getByTestId("studio-status")).toHaveAttribute(
      "data-state",
      "connecting",
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    expect(screen.getByTestId("studio-status")).toHaveAttribute(
      "data-state",
      "needs-key",
    );
    await passGrace();
    expect(screen.getByTestId("studio-connect-notice")).toHaveAttribute(
      "data-kind",
      "needs-key",
    );
    expect(posted.some((p) => p.message.type === "embed.key")).toBe(false);
  });

  it("goes back quickly when Studio refuses a fresh link's key", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    planLink();
    renderStudio();
    const element = frame();
    spyOnFrame(element);
    await fromChild(element, HELLO);
    await fromChild(element, { type: "embed.gate", state: "refused" });
    await passGrace();
    expect(screen.getByTestId("studio-connect-notice")).toHaveAttribute(
      "data-kind",
      "refused",
    );
  });
});
