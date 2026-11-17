/* eslint-env node */
// The connect screen tells the truth about links that cannot open Studio and
// takes a paste at once:
// - omega dev, when its output is not a terminal, prints its local link as
//   the literal "Open http://127.0.0.1:<port>/#t=<key>"; only its "Open in
//   Future AGI" line holds the key. That line is refused at the field with
//   what to do instead, never handed to Studio as a key.
// - An "Open in Future AGI" link that lost its key (cut off where the
//   terminal wrapped it) says so, instead of opening a silent screen.
// - The paste field has focus whenever the screen appears, so ⌘V and Enter
//   are all it takes, and the notice that says why is read with it.
import fs from "node:fs";
import path from "node:path";
import { act, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  HELLO,
  SERVER,
  TOKEN,
  frame,
  fromChild,
  planLink,
  renderStudio,
  resetStudioTestState,
  spyOnFrame,
} from "./studio-test-utils";

// The real first script of frontend/index.html, run as the browser would.
const INDEX_HTML = fs.readFileSync(
  path.resolve(__dirname, "../../../../../index.html"),
  "utf8",
);
const intake = new DOMParser()
  .parseFromString(INDEX_HTML, "text/html")
  .querySelector("script[data-omega-connect-intake]");
const runIntake = () => {
  // eslint-disable-next-line no-new-func
  new Function(intake.textContent)();
};
const visit = (url) => window.history.replaceState(null, "", url);
// What index.html leaves for a connect link without a usable key.
const MARK = "__omegaConnectKeyless";

const READY = { type: "embed.gate", state: "ready" };

// omega dev's listing when its output is not a terminal (dev-print.ts).
const PLACEHOLDER_LINE = `Open ${SERVER}/#t=<key>`;
const FUTURE_AGI_LINK = `https://app.futureagi.com/dashboard/studio#connect=${encodeURIComponent(
  SERVER,
)}&t=${TOKEN}`;
const LISTING = [
  "Omega Studio · running on this machine",
  `Omega Studio listening at ${SERVER}`,
  PLACEHOLDER_LINE,
  "  The key is left out because this output is not a terminal and pages may frame Studio.",
  `Open in Future AGI: ${FUTURE_AGI_LINK}`,
  "  Pages on https://app.futureagi.com may frame Studio; the embed protocol hands a framed page its key over postMessage",
  `API ${SERVER}/studio/v1 (Authorization: Bearer <key>) · docs ${SERVER}/docs`,
].join("\n");

const input = () => screen.getByTestId("studio-connect-input");
const notice = () => screen.getByTestId("studio-connect-notice");
const describedBy = (element) =>
  (element.getAttribute("aria-describedby") || "").split(/\s+/);
const keys = (posted) => posted.filter((p) => p.message.type === "embed.key");

const paste = (text) => {
  fireEvent.change(input(), { target: { value: text } });
  fireEvent.click(screen.getByTestId("studio-connect-submit"));
};

beforeEach(() => {
  resetStudioTestState();
  delete window[MARK];
  visit("/dashboard/studio");
  globalThis.fetch = vi.fn(() => Promise.reject(new TypeError("offline")));
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  resetStudioTestState();
  delete window[MARK];
  visit("/");
});

describe("Studio connect screen: links without a key", () => {
  it("'#t=<key>' link refused at once with the right next step", () => {
    renderStudio();
    paste(PLACEHOLDER_LINE);

    // Refused at the field: nothing is framed and no key check starts.
    expect(screen.queryByTestId("omega-embed-frame")).toBeNull();
    expect(screen.getByTestId("studio-connect")).toBeInTheDocument();
    const field = input();
    expect(field).toHaveAttribute("aria-invalid", "true");
    const help = screen.getByText(/^This link shows <key> instead of the key/);
    expect(help).toHaveTextContent(
      "because omega dev leaves the key out when its output isn't a terminal. Paste its “Open in Future AGI” line instead, or run omega dev in a terminal.",
    );
    expect(describedBy(field)).toContain(help.id);
    // The field keeps focus with the line selected, so the right line pastes
    // straight over it.
    expect(field).toHaveFocus();
    expect(field.selectionStart).toBe(0);
    expect(field.selectionEnd).toBe(PLACEHOLDER_LINE.length);

    // The bare link, as typed or percent-encoded, gets the same answer.
    for (const link of [`${SERVER}/#t=<key>`, `${SERVER}/#t=%3Ckey%3E`]) {
      paste(link);
      expect(screen.queryByTestId("omega-embed-frame")).toBeNull();
      expect(
        screen.getByText(/^This link shows <key> instead of the key/),
      ).toBeVisible();
    }
  });

  it("connects a pasted non-terminal listing with its 'Open in Future AGI' line, not the '#t=<key>' line above it", async () => {
    renderStudio();
    paste(LISTING);

    const element = frame();
    expect(element).toHaveAttribute("src", `${SERVER}/?embed=1`);
    const posted = spyOnFrame(element);
    await fromChild(element, HELLO);
    expect(keys(posted)).toEqual([
      { message: { type: "embed.key", token: TOKEN }, targetOrigin: SERVER },
    ]);
  });

  it("a link that lost its key opens with a clear message", () => {
    // Opened from the terminal with its "&t=..." cut off: the address bar is
    // cleaned and nothing is held, so the tab has no server to open.
    visit(`/dashboard/studio#connect=${encodeURIComponent(SERVER)}`);
    runIntake();
    expect(window.location.hash).toBe("");
    expect(Object.keys(window)).not.toContain(MARK);

    renderStudio();

    expect(screen.queryByTestId("omega-embed-frame")).toBeNull();
    const shown = notice();
    expect(shown).toHaveAttribute("data-kind", "link-without-key");
    expect(shown).toHaveTextContent("That link had no key");
    expect(shown).toHaveTextContent(
      "It may have been cut off when it was copied. Copy the whole “Open in Future AGI” line omega dev printed and open it again, or paste it below.",
    );
    // The reason is read with the field, which is ready for the paste.
    expect(input()).toHaveFocus();
    expect(describedBy(input())).toContain(shown.id);
    // The mark is read once the screen shows, then forgotten.
    expect(window[MARK]).toBeUndefined();
  });

  it("says so under StrictMode too, whose double render must not lose the mark", () => {
    visit(`/dashboard/studio#connect=${encodeURIComponent(SERVER)}&t=`);
    runIntake();

    renderStudio({ strict: true });

    expect(notice()).toHaveAttribute("data-kind", "link-without-key");
    expect(input()).toHaveFocus();
  });

  it("says so when such a link is opened with the connect screen already showing, then connects from the whole link", async () => {
    runIntake();
    renderStudio();
    expect(screen.queryByTestId("studio-connect-notice")).toBeNull();
    act(() => input().blur());

    // A link cut off inside its key's escape, by fragment navigation.
    act(() => {
      visit(`/dashboard/studio#connect=${encodeURIComponent(SERVER)}&t=%E0%A4%A`);
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });

    expect(window.location.hash).toBe("");
    const shown = notice();
    expect(shown).toHaveAttribute("data-kind", "link-without-key");
    expect(input()).toHaveFocus();
    expect(describedBy(input())).toContain(shown.id);
    // Marked only while its event ran: a connect screen shown later says
    // nothing about it.
    expect(window[MARK]).toBeUndefined();

    // The whole line connects, and hands over its key once.
    act(() => {
      visit(`/dashboard/studio#connect=${encodeURIComponent(SERVER)}&t=${TOKEN}`);
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    const element = frame();
    const posted = spyOnFrame(element);
    await fromChild(element, HELLO);
    expect(keys(posted)).toEqual([
      { message: { type: "embed.key", token: TOKEN }, targetOrigin: SERVER },
    ]);
  });
});

describe("Studio connect screen: the paste field takes the paste", () => {
  it("paste field focused on open", () => {
    renderStudio();
    expect(input()).toHaveFocus();
    expect(input()).not.toHaveAttribute("aria-describedby");
  });

  it("focuses the paste field again when Disconnect brings the connect screen back", async () => {
    planLink();
    renderStudio();
    const element = frame();
    spyOnFrame(element);
    await fromChild(element, HELLO);
    await fromChild(element, READY);
    const disconnect = screen.getByTestId("studio-disconnect");
    act(() => disconnect.focus());

    fireEvent.click(disconnect);

    expect(screen.getByTestId("studio-connect")).toBeInTheDocument();
    expect(input()).toHaveFocus();
  });

  it("focuses the field under a notice that says why a link was refused", () => {
    planLink("https://evil.example", TOKEN);
    renderStudio();
    const shown = notice();
    expect(shown).toHaveAttribute("data-kind", "not-local");
    expect(input()).toHaveFocus();
    expect(describedBy(input())).toEqual([shown.id]);
  });
});
