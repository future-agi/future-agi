import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import DisclosurePanel from "../components/DisclosurePanel";
import { DISCLOSURE_PARTS } from "../lib/disclosure";

afterEach(cleanup);

// A confirmed disclosure names what an entry touches: environment variables,
// hosts, file paths and URLs, key names. On the entry page it sits in a
// narrow side column, so each name is set in the code face, on one line in
// a box of its own, while the prose around it wraps at its spaces. A line
// may break between names only after a "/" that joins two whole names, and
// the punctuation right against a name stays on the name's line. A long
// part shows its opening words and opens whole in a full-width dialog.

const disclosure = {
  summary: "Answers a weather request with the tool in tools/forecast.mjs.",
  network:
    "Calls api.openai.com and https://weather.example.test/v1/forecast only.",
  files: "Reads ~/.omega/config.json and writes nothing outside the entry.",
  shell: "None.",
  background: "None.",
  credentials: "Reads OPENAI_API_KEY (or WEATHER_API_KEY) and nothing else.",
};

// The three Google credential variables one after another, and the
// Credentials part that names them, as the Official entries write it.
const GOOGLE =
  "OMEGA_VERTEX_CREDS/OMEGA_VERTEX_SA/GOOGLE_APPLICATION_CREDENTIALS";
const OFFICIAL = `Live: OPENAI_API_KEY/ANTHROPIC_API_KEY or Google service-account file (first: ${GOOGLE}); signs assertion locally for access token; no key stored.`;

// A part's value: the <dd> right after its label.
const valueOf = (label) => screen.getByText(label).nextElementSibling;

const names = (node) =>
  [...node.querySelectorAll("code")].map((code) => code.textContent);

// Where a line may break inside the names of `node`: for each <wbr>, the
// word that ends there (the text since the last space).
function breaksIn(node) {
  const breaks = [];
  let text = "";
  const walker = document.createTreeWalker(node, NodeFilter.SHOW_ALL);
  for (let at = walker.nextNode(); at; at = walker.nextNode()) {
    if (at.nodeName === "WBR") breaks.push(text.split(/\s/).pop());
    else if (at.nodeType === Node.TEXT_NODE) text += at.textContent;
  }
  return breaks;
}

// The line a name holds: the element around the box of the name that ends
// in `name`, with the punctuation right against it.
function lineOf(value, name) {
  for (const code of value.querySelectorAll("code"))
    if (code.textContent.endsWith(name)) return code.parentElement;
  return null;
}

const breaksAnywhere = (node) =>
  getComputedStyle(node).overflowWrap === "anywhere";

// A name is a box of its own on one line, with no break point inside it.
function expectWholeName(code) {
  const style = getComputedStyle(code);
  expect(style.whiteSpace).toBe("nowrap");
  expect(code.children).toHaveLength(0);
  expect(breaksAnywhere(code)).toBe(false);
  expect(style.wordBreak).not.toBe("break-all");
}

describe("Names in the disclosure panel", () => {
  it("sets each variable, host, path and URL in the code face, and keeps the text as confirmed", () => {
    render(<DisclosurePanel disclosure={disclosure} />);
    expect(names(valueOf("Network"))).toEqual([
      "api.openai.com",
      "https://weather.example.test/v1/forecast",
    ]);
    expect(names(valueOf("Files"))).toEqual(["~/.omega/config.json"]);
    expect(names(valueOf("Credentials"))).toEqual([
      "OPENAI_API_KEY",
      "WEATHER_API_KEY",
    ]);
    const summary = screen.getByTestId("hub-disclosure-summary");
    expect(names(summary)).toEqual(["tools/forecast.mjs"]);
    expect(summary.textContent).toBe(disclosure.summary);
    for (const part of DISCLOSURE_PARTS)
      expect(valueOf(part.label).textContent).toBe(disclosure[part.key]);
  });

  it("breaks names joined by '/' only after a '/' between two whole names, never inside one", () => {
    render(
      <DisclosurePanel disclosure={{ ...disclosure, credentials: OFFICIAL }} />,
    );
    const value = valueOf("Credentials");
    expect(breaksIn(value)).toEqual([
      "OPENAI_API_KEY/",
      "OMEGA_VERTEX_CREDS/",
      "OMEGA_VERTEX_CREDS/OMEGA_VERTEX_SA/",
    ]);
    expect(names(value)).toEqual([
      "OPENAI_API_KEY/",
      "ANTHROPIC_API_KEY",
      "OMEGA_VERTEX_CREDS/",
      "OMEGA_VERTEX_SA/",
      "GOOGLE_APPLICATION_CREDENTIALS",
    ]);
    expect(names(value).slice(2).join("")).toBe(GOOGLE);
    expect(value.textContent).toBe(OFFICIAL);
    for (const code of value.querySelectorAll("code")) expectWholeName(code);
    for (const part of DISCLOSURE_PARTS)
      expect(breaksAnywhere(valueOf(part.label))).toBe(false);
  });

  it("keeps a host, a path and a URL whole: a line never breaks inside one", () => {
    const network =
      "Calls api.openai.com, oauth2.googleapis.com/token and https://weather.example.test/v1/forecast only.";
    const files =
      "Reads ~/.omega/config.json, scripts/verified.json and $HOME/.omega/cache.";
    render(<DisclosurePanel disclosure={{ ...disclosure, network, files }} />);
    expect(names(valueOf("Network"))).toEqual([
      "api.openai.com",
      "oauth2.googleapis.com/token",
      "https://weather.example.test/v1/forecast",
    ]);
    expect(names(valueOf("Files"))).toEqual([
      "~/.omega/config.json",
      "scripts/verified.json",
      "$HOME/.omega/cache",
    ]);
    const summary = screen.getByTestId("hub-disclosure-summary");
    expect(names(summary)).toEqual(["tools/forecast.mjs"]);
    for (const value of [summary, ...document.querySelectorAll("dd")]) {
      expect(breaksIn(value)).toEqual([]);
      for (const code of value.querySelectorAll("code")) expectWholeName(code);
    }
  });

  it("keeps the punctuation right against a name on the name's line, so no line starts with ')' or ';'", () => {
    const shell = `Live OPENAI_API_KEY/ANTHROPIC_API_KEY or Google service-account file via ${GOOGLE}; signs tokens locally; no key stored.`;
    const files = "Writes (OMEGA_LIVE_SPEND_LOG) and nothing else.";
    render(
      <DisclosurePanel
        disclosure={{ ...disclosure, credentials: OFFICIAL, shell, files }}
      />,
    );
    const last = "GOOGLE_APPLICATION_CREDENTIALS";
    const spend = "OMEGA_LIVE_SPEND_LOG";
    for (const [label, name, line] of [
      ["Credentials", last, `${last});`],
      ["Shell", last, `${last};`],
      ["Files", spend, `(${spend})`],
      ["Network", "api.openai.com", "api.openai.com"],
    ]) {
      const held = lineOf(valueOf(label), name);
      expect(getComputedStyle(held).whiteSpace).toBe("nowrap");
      expect(held.textContent).toBe(line);
    }
  });

  it("scrolls a long name with no separator sideways inside its own box, never breaking it inside a word or widening the panel", () => {
    const long = "$OMEGAHUBMIRRORREGISTRYCREDENTIALSFORTHEBUILDRUNNER";
    const credentials = `Reads ${long} and nothing else.`;
    render(<DisclosurePanel disclosure={{ ...disclosure, credentials }} />);
    const value = valueOf("Credentials");
    expect(names(value)).toEqual([long]);
    expect(breaksIn(value)).toEqual([]);
    for (const name of document.querySelectorAll("code")) {
      const style = getComputedStyle(name);
      expect(style.overflowX).toBe("auto");
      expect(style.display).toBe("inline-block");
      expect(style.maxWidth).toBe("100%");
      expectWholeName(name);
    }
  });

  it("leaves plain words as prose: and/or, e.g., read-only, a time and a number", () => {
    const credentials =
      "Reads a key and/or a token, e.g. at start-up, read-only, at 10:30 or 1.5 times a day.";
    render(<DisclosurePanel disclosure={{ ...disclosure, credentials }} />);
    const value = valueOf("Credentials");
    expect(names(value)).toEqual([]);
    expect(value.textContent).toBe(credentials);
  });

  it("opens a long part whole in a full-width dialog from Show all, its names still in the code face", async () => {
    const network = `${"Calls api.openai.com for each answer. ".repeat(10)}Then reads WEATHER_API_KEY.`;
    render(<DisclosurePanel disclosure={{ ...disclosure, network }} />);
    const value = valueOf("Network");
    expect(value.textContent).not.toContain("WEATHER_API_KEY");
    const show = within(value).getByRole("button", {
      name: "Show all network disclosure",
    });
    expect(show).toHaveAttribute("aria-haspopup", "dialog");
    fireEvent.click(show);
    const dialog = await screen.findByRole("dialog", {
      name: "Disclosure: Network",
    });
    expect(dialog).toHaveClass("MuiDialog-paperFullWidth");
    const full = within(dialog).getByTestId("hub-disclosure-full");
    expect(full.textContent).toBe(network);
    expect(names(full)).toContain("WEATHER_API_KEY");
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});
