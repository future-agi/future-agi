import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { offerOmegaConnect } from "src/sections/omega/embed";

import {
  ACTIVE_CONNECTION_KEY,
  SAVED_CONNECTIONS_KEY,
  readSavedConnections,
} from "../saved-connections";
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

beforeEach(() => {
  resetStudioTestState();
  globalThis.fetch = vi.fn(() => Promise.reject(new TypeError("offline")));
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  resetStudioTestState();
});

describe("Studio connect screen", () => {
  it("shows the paste field, the exact command for this site, and no saved list when there is none", () => {
    renderStudio();
    expect(screen.getByTestId("studio-connect")).toBeInTheDocument();
    expect(screen.queryByTestId("omega-embed-frame")).toBeNull();
    expect(screen.getByTestId("studio-start-command-text")).toHaveTextContent(
      `omega dev --no-open --embed-origin ${window.location.origin}`,
    );
    expect(screen.getByTestId("studio-start-signin")).toHaveTextContent(
      "sign in, then open the “Open in Future AGI” link again",
    );
    expect(screen.queryByTestId("studio-saved")).toBeNull();
    // The field is kept out of autocapture, replay and form history.
    const input = screen.getByTestId("studio-connect-input");
    expect(input).toHaveAttribute("autocomplete", "off");
    expect(input.closest(".ph-no-capture.rr-block")).not.toBeNull();
  });

  it("copies the command", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    renderStudio();
    await act(async () => {
      fireEvent.click(screen.getByTestId("studio-start-command-copy"));
    });
    expect(writeText).toHaveBeenCalledWith(
      `omega dev --no-open --embed-origin ${window.location.origin}`,
    );
  });

  it("refuses a link that does not point to this computer, and frames nothing", () => {
    renderStudio();
    fireEvent.change(screen.getByTestId("studio-connect-input"), {
      target: { value: "https://studio.example.com/#t=abc" },
    });
    fireEvent.click(screen.getByTestId("studio-connect-submit"));
    expect(
      screen.getByText(/its address starts with http:\/\/127\.0\.0\.1/),
    ).toBeVisible();
    expect(screen.queryByTestId("omega-embed-frame")).toBeNull();
  });

  it("connects from a pasted link, hands its key over on hello, and stores the URL only", async () => {
    renderStudio();
    fireEvent.change(screen.getByTestId("studio-connect-input"), {
      target: { value: `${SERVER}/#t=${TOKEN}` },
    });
    fireEvent.click(screen.getByTestId("studio-connect-submit"));

    const element = frame();
    expect(element).toHaveAttribute("src", `${SERVER}/?embed=1`);
    expect(screen.getByTestId("studio-connected")).toHaveAttribute(
      "data-source",
      "pasted",
    );
    const posted = spyOnFrame(element);
    await fromChild(element, HELLO);
    expect(posted[0]).toEqual({
      message: { type: "embed.key", token: TOKEN },
      targetOrigin: SERVER,
    });
    await fromChild(element, { type: "embed.gate", state: "ready" });
    await waitFor(() =>
      expect(screen.getByTestId("studio-status")).toHaveAttribute(
        "data-state",
        "connected",
      ),
    );

    // A duplicate hello from the same document gets no key: sent once.
    // A reloaded document also reports needs-key (StudioPage.reload.test.jsx).
    await fromChild(element, HELLO);
    expect(posted.filter((p) => p.message.type === "embed.key")).toHaveLength(
      1,
    );

    expect(readSavedConnections()).toEqual([
      { url: SERVER, label: "127.0.0.1:4191" },
    ]);
    expect(session().getItem(ACTIVE_CONNECTION_KEY)).toBe(SERVER);
    expect(allStorageText()).not.toContain(TOKEN);
    expect(JSON.parse(local().getItem(SAVED_CONNECTIONS_KEY))).toEqual([
      { url: SERVER, label: "127.0.0.1:4191" },
    ]);
  });

  it("opens the link index.html took out of the address bar and frames that server", async () => {
    planLink();
    renderStudio({ path: "/dashboard/studio/t/th_1?run=r_1" });
    const element = frame();
    // The parent route travels to the child as its first route.
    expect(element).toHaveAttribute(
      "src",
      `${SERVER}/?embed=1#/t/th_1?run=r_1`,
    );
    expect(element.getAttribute("src")).not.toContain(TOKEN);
    expect(screen.getByTestId("studio-connected")).toHaveAttribute(
      "data-source",
      "link",
    );
    const posted = spyOnFrame(element);
    await fromChild(element, HELLO);
    expect(posted[0].message).toEqual({ type: "embed.key", token: TOKEN });
    expect(posted[0].targetOrigin).toBe(SERVER);
    expect(window.__omegaConnect ?? null).toBeNull();
  });

  it("opens a server handed over in memory (Deployments' Open in Studio), with no token in any URL, and its key once (OD-21)", async () => {
    expect(offerOmegaConnect(SERVER, TOKEN)).toBe(true);
    renderStudio();
    const element = frame();
    expect(element).toHaveAttribute("src", `${SERVER}/?embed=1`);
    expect(screen.getByTestId("studio-connected")).toHaveAttribute(
      "data-source",
      "link",
    );
    expect(window.location.href).not.toContain(TOKEN);
    const posted = spyOnFrame(element);
    // A hello from any other origin gets nothing.
    await fromChild(element, HELLO, "http://127.0.0.1:9999");
    expect(posted).toEqual([]);
    await fromChild(element, HELLO);
    expect(posted[0].message).toEqual({ type: "embed.key", token: TOKEN });
    expect(posted[0].targetOrigin).toBe(SERVER);
    await fromChild(element, HELLO);
    expect(posted.filter((p) => p.message.type === "embed.key")).toHaveLength(
      1,
    );
    expect(allStorageText()).not.toContain(TOKEN);
  });

  it("refuses a link whose server is not on this computer and says why", () => {
    planLink("https://evil.example", TOKEN);
    renderStudio();
    expect(screen.queryByTestId("omega-embed-frame")).toBeNull();
    expect(screen.getByTestId("studio-connect-notice")).toHaveAttribute(
      "data-kind",
      "not-local",
    );
  });

  it("mirrors the child's route into the address bar without remounting the frame", async () => {
    planLink();
    renderStudio();
    const element = frame();
    spyOnFrame(element);
    await fromChild(element, HELLO);
    await fromChild(element, { type: "embed.route", path: "/t/th_9?run=r_9" });
    await waitFor(() =>
      expect(`${probe.location.pathname}${probe.location.search}`).toBe(
        "/dashboard/studio/t/th_9?run=r_9",
      ),
    );
    // Same frame element: moving between /dashboard/studio and
    // /dashboard/studio/* keeps the connection.
    expect(frame()).toBe(element);
    expect(screen.getByTestId("studio-connected")).toBeInTheDocument();
  });

  it("switches to a new link opened while the page is showing", async () => {
    planLink();
    renderStudio();
    expect(frame()).toHaveAttribute("src", `${SERVER}/?embed=1`);
    const other = "http://127.0.0.1:4300";
    act(() => {
      planLink(other, "second-token");
      window.dispatchEvent(new Event("omega:connect"));
    });
    await waitFor(() =>
      expect(frame()).toHaveAttribute("src", `${other}/?embed=1`),
    );
    const posted = spyOnFrame(frame());
    await fromChild(frame(), HELLO, other);
    expect(posted[0]).toEqual({
      message: { type: "embed.key", token: "second-token" },
      targetOrigin: other,
    });
  });

  it("disconnects back to the connect screen and forgets the active server", async () => {
    planLink();
    renderStudio({ path: "/dashboard/studio/t/th_1" });
    const element = frame();
    spyOnFrame(element);
    await fromChild(element, HELLO);
    await fromChild(element, { type: "embed.gate", state: "ready" });
    await waitFor(() =>
      expect(session().getItem(ACTIVE_CONNECTION_KEY)).toBe(SERVER),
    );
    fireEvent.click(screen.getByTestId("studio-disconnect"));
    expect(screen.getByTestId("studio-connect")).toBeInTheDocument();
    expect(session().getItem(ACTIVE_CONNECTION_KEY)).toBeNull();
    expect(probe.location.pathname).toBe("/dashboard/studio");
    // The saved connection stays for next time.
    expect(screen.getByTestId("studio-saved-row")).toHaveAttribute(
      "data-url",
      SERVER,
    );
  });

  it("does not reconnect to a link after Disconnect when the tab is opened again", async () => {
    planLink();
    const first = renderStudio();
    const element = frame();
    spyOnFrame(element);
    await fromChild(element, HELLO);
    fireEvent.click(screen.getByTestId("studio-disconnect"));
    first.unmount();
    renderStudio();
    expect(screen.getByTestId("studio-connect")).toBeInTheDocument();
    expect(screen.queryByTestId("omega-embed-frame")).toBeNull();
  });

  it("shows the link's server after leaving the tab but cannot replay its spent key", async () => {
    planLink();
    const first = renderStudio();
    const element = frame();
    spyOnFrame(element);
    await fromChild(element, HELLO);
    first.unmount();
    renderStudio();
    expect(frame()).toHaveAttribute("src", `${SERVER}/?embed=1`);
    const posted = spyOnFrame(frame());
    await fromChild(frame(), HELLO);
    // The key was handed out once already; the remounted child has none.
    expect(posted.some((p) => p.message.type === "embed.key")).toBe(false);
    await fromChild(frame(), { type: "embed.gate", state: "needs-key" });
    expect(screen.getByTestId("omega-embed-gate")).toHaveAttribute(
      "data-state",
      "needs-key",
    );
    expect(screen.getByTestId("omega-embed-gate")).toHaveTextContent(
      "Local Studio needs its key",
    );
  });

  it("opens local Studio in a tab of its own, without the key", () => {
    planLink();
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    renderStudio();
    fireEvent.click(screen.getByTestId("studio-open-local"));
    expect(open).toHaveBeenCalledWith(
      `${SERVER}/`,
      "_blank",
      "noopener,noreferrer",
    );
  });
});
