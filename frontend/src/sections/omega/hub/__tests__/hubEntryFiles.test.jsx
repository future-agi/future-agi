import React from "react";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";

import rules from "../__fixtures__/rules.json";
import official from "../__fixtures__/entry-official.json";
import versions from "../__fixtures__/versions.json";
import meUser from "../__fixtures__/me-user.json";
import HubEntryPage from "../HubEntryPage";
import { respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));
vi.mock("src/config-global", () => ({ HOST_API: "http://localhost:8000" }));

// The Files tab as a visitor reads it, like the file view of a model or
// package page: the version's files with their sizes beside the open one,
// under a bar that names it, says how many lines it has and how big it is,
// and copies it whole in one click. A version with no files says so and
// offers the next thing to read; a file longer than the registry shows
// says only its start is shown and where the whole of it is, and its bar
// gives its size with no line count; a file row reached by keyboard shows
// where focus is.

const ENTRY = "/omega-hub/v1/entries/future-agi/first-agent";
const AT = "/dashboard/hub/future-agi/first-agent";
const README = `${ENTRY}/versions/1.0.0/files/README.md`;

function serve(entry = structuredClone(official), extra = []) {
  respond([
    ...extra,
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

const open = (at = `${AT}/files`) =>
  renderAt(<HubEntryPage />, {
    path: "/dashboard/hub/:publisher/:name/:tab?",
    at,
  });

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

const currentPath = () => screen.getByTestId("location").textContent;

// The style rule that draws `node`'s keyboard focus, if any.
function focusRuleOf(node) {
  const styleRules = Array.from(document.styleSheets).flatMap((sheet) =>
    Array.from(sheet.cssRules || []),
  );
  return styleRules.find((rule) => {
    const selectors = (rule.selectorText || "")
      .split(",")
      .map((selector) => selector.trim());
    return Array.from(node.classList).some((name) =>
      selectors.includes(`.${name}.Mui-focusVisible`),
    );
  });
}

// The latest version with no files, on an entry with another version to
// point to (an entry with only one is in hubEntryFilesWording).
const withoutFiles = (changes = {}) => {
  const entry = structuredClone(official);
  entry.versionCount = 2;
  entry.latest = { ...entry.latest, files: [], ...changes };
  return entry;
};

beforeEach(() => vi.stubEnv("VITE_OMEGA_CONSOLE", "1"));
afterEach(() => vi.unstubAllEnvs());

describe("Files tab of a version with no files", () => {
  it("says the version has none, with no empty file list, and opens the Versions table in one click", async () => {
    serve(withoutFiles());
    open();
    const empty = await screen.findByTestId("hub-entry-files-empty");
    expect(empty).toHaveAttribute("role", "status");
    expect(
      within(empty).getByRole("heading", { level: 3, name: "No files" }),
    ).toBeInTheDocument();
    expect(empty).toHaveTextContent(
      "Version 1.0.0 has no files to show. Read its README, or open another version from the Versions table.",
    );
    expect(screen.queryByTestId("hub-file-tree")).toBeNull();
    expect(screen.queryByText("Choose a file to read it.")).toBeNull();
    fireEvent.click(
      within(empty).getByRole("button", { name: "See all versions" }),
    );
    await waitFor(() => expect(currentPath()).toBe(`${AT}/versions`));
    expect(await screen.findByTestId("hub-versions")).toBeInTheDocument();
  });

  it("opens the README in one click", async () => {
    serve(withoutFiles());
    open();
    const empty = await screen.findByTestId("hub-entry-files-empty");
    fireEvent.click(
      within(empty).getByRole("button", { name: "Open the README" }),
    );
    await waitFor(() => expect(currentPath()).toBe(AT));
    expect(await screen.findByTestId("hub-readme")).toHaveTextContent(
      "Run it with your key",
    );
  });

  it("with no README either, points only to the Versions table", async () => {
    serve(withoutFiles({ readme: "" }));
    open();
    const empty = await screen.findByTestId("hub-entry-files-empty");
    expect(empty).toHaveTextContent(
      "Version 1.0.0 has no files to show. Open another version from the Versions table.",
    );
    expect(
      within(empty).queryByRole("button", { name: "Open the README" }),
    ).toBeNull();
    expect(
      within(empty).getByRole("button", { name: "See all versions" }),
    ).toBeInTheDocument();
  });
});

describe("Files tab: the open file", () => {
  it("the bar names the file, its line count and its size, and copies it whole", async () => {
    const writeText = clipboard();
    const text = official.latest.readme;
    serve(structuredClone(official), [["get", README, text]]);
    open();
    const name = await screen.findByRole("heading", {
      level: 3,
      name: "README.md",
    });
    const viewer = await screen.findByTestId("hub-file-viewer");
    await waitFor(() =>
      expect(viewer).toHaveTextContent("Your first agent in one file"),
    );
    const lines = text.replace(/\n$/, "").split("\n").length;
    const bar = name.parentElement;
    await waitFor(() =>
      expect(bar).toHaveTextContent(`${lines} lines · 1.4 KB`),
    );
    fireEvent.click(
      within(bar).getByRole("button", {
        name: "Copy the contents of README.md",
      }),
    );
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(text));
  });

  it("a file longer than the registry shows says only its start is shown, gives its size with no line count, and links the archive that holds all of it", async () => {
    const entry = structuredClone(official);
    entry.latest.files = [
      { lang: "javascript", path: "main.mjs", bytes: 200 * 1024 },
    ];
    serve(entry, [
      [
        "get",
        `${ENTRY}/versions/1.0.0/files/main.mjs`,
        "const x = 1;\n".repeat(20),
      ],
    ]);
    open();
    const note = await screen.findByTestId("hub-file-partial");
    expect(note).toHaveTextContent(
      "This file is 200.0 KB; the first 64 KB is shown. Download the archive for the whole file.",
    );
    expect(
      within(note).getByRole("link", { name: "Download the archive" }),
    ).toHaveAttribute(
      "href",
      `http://localhost:8000${ENTRY}/versions/1.0.0/bundle?source=web`,
    );
    // Its bar counts no lines: the lines shown are not all of them.
    expect(screen.getByTestId("hub-file-facts")).toHaveTextContent(
      /^200\.0 KB$/,
    );
    // Its start is not offered as the whole file.
    expect(
      screen.queryByRole("button", { name: "Copy the contents of main.mjs" }),
    ).toBeNull();
  });

  it("a file row reached by keyboard shows the focus ring inside its edge", async () => {
    serve(structuredClone(official), [["get", README, official.latest.readme]]);
    open();
    const [row] = await screen.findAllByTestId("hub-file-row");
    const rule = focusRuleOf(row);
    expect(rule).toBeDefined();
    expect(rule.style.getPropertyValue("outline")).toMatch(/^2px solid \S/);
    expect(rule.style.getPropertyValue("outline-offset")).toBe("-2px");
  });
});
