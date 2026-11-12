// What the Studio tab says when it has no key to hand over, and where it goes
// next. The key lives only in memory (the tab's and the child's), so a saved
// connection, a bare address, a reload or a trip to another Future AGI page
// all end with Studio asking for the link again. The tab says so in terms of
// the link, offers the one step that can work (paste it), and keeps the page
// the person was on so pasting the link reopens it.
import { act, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ACTIVE_CONNECTION_KEY,
  SAVED_CONNECTIONS_KEY,
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
  probe,
  renderStudio,
  resetStudioTestState,
  spyOnFrame,
  storages,
} from "./studio-test-utils";

const [local, session] = [() => storages()[0], () => storages()[1]];
const READY = { type: "embed.gate", state: "ready" };
const REFUSED = { type: "embed.gate", state: "refused" };
const NEEDS_KEY = { type: "embed.gate", state: "needs-key" };
// Deterministic, format-valid test fixture; not a generated access token.
const FORGED = "B".repeat(TOKEN.length);

const keys = (posted) => posted.filter((p) => p.message.type === "embed.key");
const gate = () => screen.getByTestId("omega-embed-gate");
const status = () => screen.getByTestId("studio-status");
const notice = () => screen.getByTestId("studio-connect-notice");
const input = () => screen.getByTestId("studio-connect-input");
const pasteLinkButton = () => screen.getByTestId("studio-gate-paste-link");
const wait = (ms) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
const passGrace = () => wait(GATE_GRACE_MS + 100);
const paste = (text) => {
  fireEvent.change(input(), { target: { value: text } });
  fireEvent.click(screen.getByTestId("studio-connect-submit"));
};
const go = (path) =>
  act(async () => {
    probe.navigate(path);
  });

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

describe("Studio tab: saved connections and leaving the tab say what is true", () => {
  it("saved connection Connect says the link is needed again", async () => {
    local().setItem(
      SAVED_CONNECTIONS_KEY,
      JSON.stringify([{ url: SERVER, label: "Support desk" }]),
    );
    renderStudio();
    // Said before anyone clicks.
    expect(screen.getByTestId("studio-saved-note")).toHaveTextContent(
      "Remembers each server's name and address, not its key. Connecting asks for the link omega dev printed.",
    );

    fireEvent.click(screen.getByTestId("studio-saved-connect"));
    const element = frame();
    const posted = spyOnFrame(element);
    // From its first moment the frame says the link is needed, and the bar
    // does not pretend to be connecting.
    expect(status()).toHaveTextContent("Needs the link");
    expect(status()).not.toHaveTextContent("Connecting…");
    expect(screen.getByTestId("omega-embed-gate-note")).toHaveTextContent(
      "A saved connection keeps the name and address, not the key, so this tab needs omega dev's link again.",
    );
    expect(pasteLinkButton()).toHaveTextContent("Paste the link");

    await fromChild(element, HELLO);
    expect(keys(posted)).toEqual([]);

    // The button goes straight to the paste field, without the wait.
    fireEvent.click(pasteLinkButton());
    expect(screen.queryByTestId("omega-embed-frame")).toBeNull();
    expect(notice()).toHaveAttribute("data-kind", "needs-key");
    expect(notice()).toHaveTextContent(
      "Support desk (127.0.0.1:4191) needs its key",
    );
    expect(notice()).toHaveTextContent(
      "A saved connection keeps the name and address, not the key",
    );
    expect(notice()).not.toHaveTextContent("Reloading");
    expect(input()).toHaveFocus();
    expect(readSavedConnections()).toEqual([
      { url: SERVER, label: "Support desk" },
    ]);

    // A bare address says the same plainly: it has no key.
    paste("127.0.0.1:4191");
    expect(screen.getByTestId("omega-embed-gate-note")).toHaveTextContent(
      "That address has no key. This tab needs the whole link omega dev printed",
    );
    expect(status()).toHaveTextContent("Needs the link");
    const bare = frame();
    const barePosted = spyOnFrame(bare);
    await fromChild(bare, HELLO);
    await fromChild(bare, NEEDS_KEY);
    expect(gate()).toHaveTextContent("That address has no key.");
    expect(screen.queryByRole("button", { name: "Reconnect" })).toBeNull();
    await passGrace();
    expect(notice()).toHaveTextContent("That address has no key.");
    expect(input()).toHaveFocus();
    expect(keys(barePosted)).toEqual([]);
  });

  it("reload/leave: honest words, same run reopens after reconnect", async () => {
    // Reload on a run: the tab restores the server, not the key.
    session().setItem(ACTIVE_CONNECTION_KEY, SERVER);
    const reloaded = renderStudio({ path: "/dashboard/studio/t/th_1" });
    const restored = frame();
    spyOnFrame(restored);
    expect(screen.getByTestId("omega-embed-gate-note")).toHaveTextContent(
      "After a reload or a visit to another page, this tab needs omega dev's link again.",
    );
    await fromChild(restored, HELLO);
    await fromChild(restored, NEEDS_KEY);
    expect(gate()).toHaveTextContent(
      "After a reload or a visit to another page, this tab needs omega dev's link again.",
    );
    expect(gate()).not.toHaveTextContent("in-memory copy");
    expect(status()).toHaveTextContent("Needs the link");
    await passGrace();
    // Back on the connect screen, still at the run.
    expect(probe.location.pathname).toBe("/dashboard/studio/t/th_1");
    expect(notice()).toHaveTextContent(
      "After a reload or a visit to another page, this tab needs omega dev's link again.",
    );
    expect(notice()).toHaveTextContent(
      "Pasting it here reopens the page you were on.",
    );
    expect(input()).toHaveFocus();

    paste(`${SERVER}/#t=${TOKEN}`);
    const again = frame();
    expect(again).toHaveAttribute("src", `${SERVER}/?embed=1#/t/th_1`);
    const posted = spyOnFrame(again);
    await fromChild(again, HELLO);
    await fromChild(again, READY);
    expect(keys(posted)).toEqual([
      { message: { type: "embed.key", token: TOKEN }, targetOrigin: SERVER },
    ]);
    expect(status()).toHaveAttribute("data-state", "connected");
    reloaded.unmount();

    // Leaving for another Future AGI page and opening Studio's home again.
    resetStudioTestState();
    planLink();
    renderStudio();
    const first = frame();
    spyOnFrame(first);
    await fromChild(first, HELLO);
    await fromChild(first, READY);
    await fromChild(first, { type: "embed.route", path: "/t/th_2" });
    expect(probe.location.pathname).toBe("/dashboard/studio/t/th_2");

    await go("/dashboard/observe");
    expect(screen.queryByTestId("omega-tab-studio")).toBeNull();
    await go("/dashboard/studio");
    const back = frame();
    const backPosted = spyOnFrame(back);
    expect(screen.getByTestId("omega-embed-gate-note")).toHaveTextContent(
      "After leaving the Studio page, this tab needs omega dev's link again.",
    );
    await fromChild(back, HELLO);
    expect(keys(backPosted)).toEqual([]);
    await fromChild(back, NEEDS_KEY);
    await passGrace();
    // The run the person was reading is the address again.
    expect(probe.location.pathname).toBe("/dashboard/studio/t/th_2");
    expect(notice()).toHaveTextContent(
      "After leaving the Studio page, this tab needs omega dev's link again.",
    );
    expect(notice()).toHaveTextContent(
      "Pasting it here reopens the page you were on.",
    );

    paste(`${SERVER}/#t=${TOKEN}`);
    expect(frame()).toHaveAttribute("src", `${SERVER}/?embed=1#/t/th_2`);
    expect(allStorageText()).not.toContain(TOKEN);
  });

  // The panel itself still hands over to the connect screen after
  // GATE_GRACE_MS (StudioPage.verify.test.jsx expects that). What stays is its
  // reason and its one working step: the same words as the connect screen's
  // notice, with the paste field focused, until the person acts.
  it("needs-key panel stays; its button works", async () => {
    planLink();
    renderStudio({ path: "/dashboard/studio/t/th_1" });
    const element = frame();
    spyOnFrame(element);
    await fromChild(element, HELLO);
    await fromChild(element, READY);
    expect(status()).toHaveAttribute("data-state", "connected");

    // Studio reloads inside the frame and has lost its key.
    await fromChild(element, HELLO);
    await fromChild(element, NEEDS_KEY);
    expect(gate()).toHaveAttribute("data-state", "needs-key");
    expect(gate()).toHaveTextContent("Local Studio needs its key");
    expect(gate()).toHaveTextContent(
      "Studio no longer has the key for this tab, so it needs omega dev's link again.",
    );
    expect(status()).toHaveTextContent("Needs the link");
    // Reconnect could only repeat the spent key; the button that can work
    // takes its place.
    expect(screen.queryByRole("button", { name: "Reconnect" })).toBeNull();
    expect(pasteLinkButton()).toBeVisible();

    // Still there after the old 1.5 s, long enough to read and use.
    await wait(1600);
    expect(gate()).toHaveAttribute("data-state", "needs-key");
    expect(pasteLinkButton()).toBeVisible();

    fireEvent.click(pasteLinkButton());
    expect(screen.queryByTestId("omega-embed-frame")).toBeNull();
    expect(input()).toHaveFocus();
    expect(notice()).toHaveAttribute("data-kind", "needs-key");
    expect(notice()).toHaveTextContent(
      "Studio no longer has the key for this tab",
    );
    expect(probe.location.pathname).toBe("/dashboard/studio/t/th_1");
    // The saved server and its label stay; the address is a bookmark.
    expect(readSavedConnections()).toEqual([
      { url: SERVER, label: "127.0.0.1:4191" },
    ]);

    // A refused key keeps its reason, even when Studio then says hello again
    // and only reports that it has no key.
    paste(`${SERVER}/#t=${FORGED}`);
    const forged = frame();
    spyOnFrame(forged);
    await fromChild(forged, HELLO);
    await fromChild(forged, REFUSED);
    expect(gate()).toHaveTextContent("Local Studio didn't accept the key");
    expect(status()).toHaveTextContent("Link not accepted");
    expect(screen.queryByRole("button", { name: "Reconnect" })).toBeNull();
    expect(pasteLinkButton()).toBeVisible();
    await fromChild(forged, HELLO);
    await fromChild(forged, NEEDS_KEY);
    await passGrace();
    expect(notice()).toHaveAttribute("data-kind", "refused");
    expect(notice()).toHaveTextContent(
      "The link may be for another omega dev, or for a key it no longer uses.",
    );
    // The words stay with the paste field until the person acts.
    await wait(20_000);
    expect(notice()).toHaveAttribute("data-kind", "refused");
    expect(input()).toHaveFocus();
    expect(probe.location.pathname).toBe("/dashboard/studio/t/th_1");
  });
});
