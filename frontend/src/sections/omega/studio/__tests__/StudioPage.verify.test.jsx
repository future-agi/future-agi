// F-HOST: the Studio tab shows "checking" until Studio's own server has
// accepted the key, shows "connected" only after that, writes nothing to
// storage before then, and keeps saved URLs when a key is missing or refused.
// RC1's framed child has no persisted key to recover on a return visit. Every
// data-state the status line takes is recorded by a MutationObserver, so a
// "connected" that lasts a single render still counts.
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
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

const FORGED = "A".repeat(TOKEN.length);
const [local, session] = [() => storages()[0], () => storages()[1]];
const READY = { type: "embed.gate", state: "ready" };
const REFUSED = { type: "embed.gate", state: "refused" };
const NEEDS_KEY = { type: "embed.gate", state: "needs-key" };

// What the tab keeps, read raw (not through saved-connections.js).
const stored = () => ({
  connections: local().getItem(SAVED_CONNECTIONS_KEY),
  active: session().getItem(ACTIVE_CONNECTION_KEY),
});
const nothingStored = { connections: null, active: null };
const seed = (list) =>
  local().setItem(SAVED_CONNECTIONS_KEY, JSON.stringify(list));

// Every data-state of the status line, and what storage held at that moment.
let observer;
let path;
function watchStatus() {
  path = [];
  const push = () => {
    const value = document
      .querySelector('[data-testid="studio-status"]')
      ?.getAttribute("data-state");
    if (value && path.at(-1)?.state !== value) {
      path.push({ state: value, stored: stored() });
    }
  };
  observer = new MutationObserver(push);
  observer.observe(document.body, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ["data-state"],
  });
}
const states = () => path.map((step) => step.state);
const status = () => screen.getByTestId("studio-status");
const notice = () => screen.getByTestId("studio-connect-notice");

const passGrace = () =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(GATE_GRACE_MS + 100);
  });

beforeEach(() => {
  resetStudioTestState();
  globalThis.fetch = vi.fn(() => Promise.reject(new TypeError("offline")));
  vi.useFakeTimers({ shouldAdvanceTime: true });
  watchStatus();
});

afterEach(() => {
  observer?.disconnect();
  vi.useRealTimers();
  vi.restoreAllMocks();
  resetStudioTestState();
});

describe("Studio tab: a key omega dev never issued (F-HOST)", () => {
  it("checks it, never shows connected, stores nothing, and settles on the refused notice", async () => {
    planLink(SERVER, FORGED);
    renderStudio();
    const element = frame();
    const posted = spyOnFrame(element);

    await fromChild(element, HELLO);
    expect(posted[0].message).toEqual({ type: "embed.key", token: FORGED });
    expect(status()).toHaveAttribute("data-state", "checking");
    expect(status()).toHaveTextContent("Checking the key…");
    expect(screen.getByTestId("omega-embed-gate")).toHaveTextContent(
      "Checking the key with local Studio…",
    );
    expect(stored()).toEqual(nothingStored);

    await fromChild(element, REFUSED);
    expect(status()).toHaveAttribute("data-state", "refused");
    await passGrace();

    expect(notice()).toHaveAttribute("data-kind", "refused");
    expect(states()).toEqual(["connecting", "checking", "refused"]);
    expect(states()).not.toContain("connected");
    for (const step of path) expect(step.stored).toEqual(nothingStored);
    expect(stored()).toEqual(nothingStored);
    expect(allStorageText()).not.toContain(SERVER);
    expect(allStorageText()).not.toContain(FORGED);
    expect(screen.queryByTestId("studio-saved")).toBeNull();
  });

  it("does the same for a pasted link", async () => {
    renderStudio();
    fireEvent.change(screen.getByTestId("studio-connect-input"), {
      target: { value: `${SERVER}/#t=${FORGED}` },
    });
    fireEvent.click(screen.getByTestId("studio-connect-submit"));
    const element = frame();
    spyOnFrame(element);
    await fromChild(element, HELLO);
    expect(stored()).toEqual(nothingStored);
    await fromChild(element, REFUSED);
    await passGrace();
    expect(notice()).toHaveAttribute("data-kind", "refused");
    expect(states()).not.toContain("connected");
    expect(stored()).toEqual(nothingStored);
  });

  it("asks for a new link when Studio never answers about the key, and stores nothing", async () => {
    planLink(SERVER, FORGED);
    renderStudio();
    const element = frame();
    spyOnFrame(element);
    await fromChild(element, HELLO);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NO_KEY_GATE_TIMEOUT_MS - 500);
    });
    expect(status()).toHaveAttribute("data-state", "checking");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    expect(status()).toHaveAttribute("data-state", "needs-key");
    await passGrace();
    expect(notice()).toHaveAttribute("data-kind", "needs-key");
    expect(states()).not.toContain("connected");
    expect(stored()).toEqual(nothingStored);
  });

  it("cannot remove a server the user saved, in a tab that never connected: a refused key that came with a link proves nothing about it", async () => {
    seed([{ url: SERVER, label: "Support agent" }]);
    planLink(SERVER, FORGED);
    renderStudio();
    const element = frame();
    spyOnFrame(element);
    await fromChild(element, HELLO);
    await fromChild(element, REFUSED);
    await passGrace();
    expect(notice()).toHaveAttribute("data-kind", "refused");
    expect(readSavedConnections()).toEqual([
      { url: SERVER, label: "Support agent" },
    ]);
  });
});

describe("Studio tab: the key omega dev printed", () => {
  it("checks it, then shows connected and stores the server (URL only) only after Studio accepted it", async () => {
    planLink();
    renderStudio();
    const element = frame();
    const posted = spyOnFrame(element);

    await fromChild(element, HELLO);
    expect(posted[0].message).toEqual({ type: "embed.key", token: TOKEN });
    expect(status()).toHaveAttribute("data-state", "checking");
    expect(stored()).toEqual(nothingStored);

    await fromChild(element, READY);
    await waitFor(() =>
      expect(status()).toHaveAttribute("data-state", "connected"),
    );
    expect(states()).toEqual(["connecting", "checking", "connected"]);
    // Nothing was written while the key was unproven.
    for (const step of path.filter((s) => s.state !== "connected")) {
      expect(step.stored).toEqual(nothingStored);
    }
    expect(readSavedConnections()).toEqual([
      { url: SERVER, label: "127.0.0.1:4191" },
    ]);
    expect(stored().active).toBe(SERVER);
    expect(allStorageText()).not.toContain(TOKEN);
  });

  it("keeps the saved URL if a live child's accepted key is later refused", async () => {
    planLink();
    renderStudio();
    const element = frame();
    spyOnFrame(element);
    await fromChild(element, HELLO);
    await fromChild(element, READY);
    expect(readSavedConnections()).toHaveLength(1);

    // The server may restart and reject the live document's key. The saved
    // URL is still useful; a gate failure is not a request to delete it.
    await fromChild(element, REFUSED);
    await passGrace();
    expect(notice()).toHaveAttribute("data-kind", "refused");
    expect(readSavedConnections()).toEqual([
      { url: SERVER, label: "127.0.0.1:4191" },
    ]);
    expect(stored().active).toBeNull();
  });
});

// A fresh link remounts the child with no previous key. A crafted link must
// fail the server's check without costing the user a saved URL or label.
describe("Studio tab: a crafted link on a return visit", () => {
  const LABEL = "Support agent";

  async function connectForReal() {
    seed([{ url: SERVER, label: LABEL }]);
    planLink();
    renderStudio();
    const element = frame();
    spyOnFrame(element);
    await fromChild(element, HELLO);
    await fromChild(element, READY);
    await waitFor(() =>
      expect(status()).toHaveAttribute("data-state", "connected"),
    );
    expect(readSavedConnections()).toEqual([{ url: SERVER, label: LABEL }]);
    expect(stored().active).toBe(SERVER);
  }

  async function freshLinkRefused() {
    const element = frame();
    const posted = spyOnFrame(element);
    await fromChild(element, HELLO);
    expect(posted[0].message).toEqual({ type: "embed.key", token: FORGED });
    expect(status()).toHaveAttribute("data-state", "checking");
    await fromChild(element, REFUSED);
    expect(status()).toHaveAttribute("data-state", "refused");
    expect(readSavedConnections()).toEqual([{ url: SERVER, label: LABEL }]);
    await passGrace();
  }

  function expectSavedServerKept() {
    expect(notice()).toHaveAttribute("data-kind", "refused");
    expect(notice()).not.toHaveTextContent("removed from your saved");
    expect(readSavedConnections()).toEqual([{ url: SERVER, label: LABEL }]);
    expect(screen.getByTestId("studio-saved")).toHaveTextContent(LABEL);
    expect(stored().active).toBeNull();
    expect(allStorageText()).not.toContain(FORGED);
    expect(allStorageText()).not.toContain(TOKEN);
  }

  it("keeps the saved server when the link arrives on the open page (hash change)", async () => {
    await connectForReal();
    const firstFrame = frame();
    planLink(SERVER, FORGED);
    await act(async () => {
      window.dispatchEvent(new Event("omega:connect"));
    });
    expect(frame()).not.toBe(firstFrame);
    await freshLinkRefused();
    expectSavedServerKept();
  });

  it("keeps the saved server when the link arrives by a full navigation in the same tab", async () => {
    // What the first visit left: the saved server, and this tab's active one.
    seed([{ url: SERVER, label: LABEL }]);
    session().setItem(ACTIVE_CONNECTION_KEY, SERVER);
    planLink(SERVER, FORGED);
    renderStudio();
    expect(screen.getByTestId("studio-connected")).toHaveAttribute(
      "data-source",
      "link",
    );
    await freshLinkRefused();
    expectSavedServerKept();
  });
});

describe("Studio tab: saved URLs are not saved credentials", () => {
  it("keeps a saved URL even if a keyless child reports refused", async () => {
    seed([
      { url: SERVER, label: "Support agent" },
      { url: "http://127.0.0.1:4300", label: "Other" },
    ]);
    renderStudio();
    fireEvent.click(screen.getAllByTestId("studio-saved-connect")[0]);
    const element = frame();
    const posted = spyOnFrame(element);
    await fromChild(element, HELLO);
    expect(posted.some((p) => p.message.type === "embed.key")).toBe(false);
    await fromChild(element, REFUSED);
    expect(readSavedConnections()).toHaveLength(2);
    await passGrace();

    expect(readSavedConnections()).toEqual([
      { url: SERVER, label: "Support agent" },
      { url: "http://127.0.0.1:4300", label: "Other" },
    ]);
    expect(stored().active).toBeNull();
    expect(states()).not.toContain("connected");
  });

  it("keeps the server a reload restored when the child needs a fresh key", async () => {
    seed([{ url: SERVER, label: "Support agent" }]);
    session().setItem(ACTIVE_CONNECTION_KEY, SERVER);
    renderStudio();
    expect(screen.getByTestId("studio-connected")).toHaveAttribute(
      "data-source",
      "restored",
    );
    const element = frame();
    spyOnFrame(element);
    await fromChild(element, HELLO);
    await fromChild(element, NEEDS_KEY);
    await passGrace();
    expect(notice()).toHaveAttribute("data-kind", "needs-key");
    expect(readSavedConnections()).toEqual([
      { url: SERVER, label: "Support agent" },
    ]);
    expect(stored().active).toBeNull();
  });

  it("connects a saved URL only after fresh link intake and the child's ready", async () => {
    seed([{ url: SERVER, label: "Support agent" }]);
    renderStudio();
    fireEvent.click(screen.getByTestId("studio-saved-connect"));
    const element = frame();
    const posted = spyOnFrame(element);
    await fromChild(element, HELLO);
    await fromChild(element, NEEDS_KEY);
    expect(posted.some((p) => p.message.type === "embed.key")).toBe(false);
    await passGrace();
    act(() => {
      planLink();
      window.dispatchEvent(new Event("omega:connect"));
    });
    const fresh = frame();
    const freshPosted = spyOnFrame(fresh);
    await fromChild(fresh, HELLO);
    expect(freshPosted[0]).toEqual({
      message: { type: "embed.key", token: TOKEN },
      targetOrigin: SERVER,
    });
    expect(status()).toHaveAttribute("data-state", "checking");
    await fromChild(fresh, READY);
    expect(status()).toHaveAttribute("data-state", "connected");
    expect(readSavedConnections()).toEqual([
      { url: SERVER, label: "Support agent" },
    ]);
    expect(stored().active).toBe(SERVER);
  });

  it("keeps a saved connection when Studio only says it has no key (nothing was refused)", async () => {
    seed([{ url: SERVER, label: "Support agent" }]);
    renderStudio();
    fireEvent.click(screen.getByTestId("studio-saved-connect"));
    const element = frame();
    spyOnFrame(element);
    await fromChild(element, HELLO);
    await fromChild(element, NEEDS_KEY);
    await passGrace();
    expect(notice()).toHaveAttribute("data-kind", "needs-key");
    expect(readSavedConnections()).toEqual([
      { url: SERVER, label: "Support agent" },
    ]);
  });

  it("clears only the active URL after a refused restored attempt", async () => {
    session().setItem(ACTIVE_CONNECTION_KEY, SERVER);
    renderStudio();
    const element = frame();
    spyOnFrame(element);
    await fromChild(element, HELLO);
    await fromChild(element, REFUSED);
    await passGrace();
    expect(notice()).toHaveAttribute("data-kind", "refused");
    expect(stored().active).toBeNull();
  });
});
