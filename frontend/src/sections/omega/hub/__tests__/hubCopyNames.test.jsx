import React from "react";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";

import rules from "../__fixtures__/rules.json";
import official from "../__fixtures__/entry-official.json";
import versions from "../__fixtures__/versions.json";
import meUser from "../__fixtures__/me-user.json";
import HubEntryPage from "../HubEntryPage";
import ReadmeView from "../components/ReadmeView";
import { respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));
vi.mock("src/config-global", () => ({ HOST_API: "http://localhost:8000" }));

// Each copy button is named for what it copies, so a screen reader user
// can tell one from the next. A README code block's button names the
// block's language when its fence names one that no other block's fence
// names, and its place among the README's code blocks when it does not; a
// shell command's button keeps the command as its name. The files digest's
// button says it copies the whole digest, instead of reading out its 64
// characters, and is as easy to hit as the other copy buttons: a target at
// least 24 px each way.

// Defined, not assigned: user-event's setup() leaves a getter-only stub.
function clipboard() {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
    writable: true,
  });
  return writeText;
}

const README = [
  "It prints:",
  "",
  "```text",
  "answer: In Lisbon today: sunny, 24 degrees.",
  "```",
  "",
  "```json",
  '{ "maxCalls": 2 }',
  "```",
  "",
  "```bash",
  "node main.mjs",
  "```",
  "",
  "```",
  "first plain block",
  "```",
  "",
  "```",
  "second plain block",
  "```",
].join("\n");

// The blocks' buttons, in order: two fences that each name a language no
// other fence names, then (after the shell command, the third block) two
// that name none.
const BLOCK_BUTTONS = [
  "Copy the text block",
  "Copy the json block",
  "Copy code block 4",
  "Copy code block 5",
];

// Two fences that name the same language, after one that names its own.
const SAME_LANGUAGE = [
  "```text",
  "answer: In Lisbon today: sunny, 24 degrees.",
  "```",
  "",
  "```json",
  '{ "maxCalls": 2 }',
  "```",
  "",
  "```json",
  '{ "maxCalls": 4 }',
  "```",
].join("\n");

describe("README code blocks: each copy button's name", () => {
  it("names the block's language when its fence names one, else its place among the README's code blocks, and no two buttons share a name", async () => {
    const writeText = clipboard();
    render(<ReadmeView markdown={README} />);
    for (const name of BLOCK_BUTTONS) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    // The shell command keeps its own button, named by the command.
    expect(
      screen.getByRole("button", { name: "Copy node main.mjs" }),
    ).toBeInTheDocument();
    const names = screen
      .getAllByRole("button")
      .map((button) => button.getAttribute("aria-label"));
    expect(names).toHaveLength(5);
    expect(new Set(names).size).toBe(names.length);
    // Each copies its own block.
    fireEvent.click(screen.getByRole("button", { name: "Copy code block 5" }));
    await waitFor(() =>
      expect(writeText).toHaveBeenLastCalledWith("second plain block"),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Copy the json block" }),
    );
    await waitFor(() =>
      expect(writeText).toHaveBeenLastCalledWith('{ "maxCalls": 2 }'),
    );
  });

  it("numbers the blocks whose fences name the same language, so no two buttons share a name", async () => {
    const writeText = clipboard();
    render(<ReadmeView markdown={SAME_LANGUAGE} />);
    const names = screen
      .getAllByRole("button")
      .map((button) => button.getAttribute("aria-label"));
    expect(names).toEqual([
      "Copy the text block",
      "Copy code block 2",
      "Copy code block 3",
    ]);
    expect(new Set(names).size).toBe(names.length);
    expect(
      screen.queryByRole("button", { name: "Copy the json block" }),
    ).toBeNull();
    // Each copies its own block.
    fireEvent.click(screen.getByRole("button", { name: "Copy code block 3" }));
    await waitFor(() =>
      expect(writeText).toHaveBeenLastCalledWith('{ "maxCalls": 4 }'),
    );
    fireEvent.click(screen.getByRole("button", { name: "Copy code block 2" }));
    await waitFor(() =>
      expect(writeText).toHaveBeenLastCalledWith('{ "maxCalls": 2 }'),
    );
  });
});

const ENTRY = "/omega-hub/v1/entries/future-agi/first-agent";
const AT = "/dashboard/hub/future-agi/first-agent";

function serve(entry = structuredClone(official)) {
  respond([
    ["get", "/api/capabilities/", capabilitiesAnswer({ parts: [] })],
    ["get", ENTRY, entry],
    ["get", `${ENTRY}/versions`, versions],
    ["get", `${ENTRY}/remixes`, []],
    ["get", "/omega-hub/v1/rules", rules],
    ["get", "/omega-hub/v1/me", meUser],
    [
      "get",
      /^\/omega-hub\/v1\/publishers\//,
      { ...entry.publisher, entryCount: 3 },
    ],
  ]);
}

const open = () =>
  renderAt(<HubEntryPage />, {
    path: "/dashboard/hub/:publisher/:name/:tab?",
    at: AT,
  });

// The copy button on Provenance's Files digest row.
async function digestButton() {
  const provenance = await screen.findByRole("region", {
    name: "Provenance",
  });
  const row = within(provenance).getByText("Files digest").nextElementSibling;
  return within(row).getByRole("button");
}

// A length in px from a computed style ("6px"), 0 when none is set.
const px = (value) => parseFloat(value) || 0;

describe("Provenance: the files digest's copy button", () => {
  beforeEach(() => vi.stubEnv("VITE_OMEGA_CONSOLE", "1"));
  afterEach(() => vi.unstubAllEnvs());

  it("is named for what it does, not by the digest's 64 characters, and copies the whole digest", async () => {
    const writeText = clipboard();
    serve();
    open();
    const copy = await digestButton();
    expect(copy).toHaveAccessibleName("Copy the whole digest");
    expect(copy.getAttribute("aria-label")).not.toContain("sha256");
    fireEvent.click(copy);
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith(official.latest.digest),
    );
  });

  it("is a target at least 24 px across and 24 px high, like the other copy buttons", async () => {
    serve();
    open();
    const copy = await digestButton();
    const box = getComputedStyle(copy);
    const glyph = getComputedStyle(copy.querySelector("svg"));
    const across = px(box.paddingLeft) + px(glyph.width) + px(box.paddingRight);
    const high = px(box.paddingTop) + px(glyph.height) + px(box.paddingBottom);
    expect(across).toBeGreaterThanOrEqual(24);
    expect(high).toBeGreaterThanOrEqual(24);
  });
});
