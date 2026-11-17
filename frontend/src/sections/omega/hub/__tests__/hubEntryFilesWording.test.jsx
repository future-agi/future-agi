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

// The Files tab's words, read as a visitor reads them. A version with no
// files says so; on an entry with only that one version there is no other
// version to point to, so the sentence ends there (with its README to read
// when it has one), while an entry with more versions still points to the
// Versions table. The README is named as the file is, README. A file longer
// than the registry shows says the limit as a whole number, 64 KB.

const ENTRY = "/omega-hub/v1/entries/future-agi/first-agent";
const AT = "/dashboard/hub/future-agi/first-agent";

function serve(entry, extra = []) {
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

const currentPath = () => screen.getByTestId("location").textContent;

// The latest version with no files, on an entry with `versionCount`
// versions.
function withoutFiles({ versionCount = 1, ...changes } = {}) {
  const entry = structuredClone(official);
  entry.versionCount = versionCount;
  entry.latest = { ...entry.latest, files: [], ...changes };
  return entry;
}

beforeEach(() => vi.stubEnv("VITE_OMEGA_CONSOLE", "1"));
afterEach(() => vi.unstubAllEnvs());

describe("Files tab of a version with no files, on an entry with one version", () => {
  it("ends at its own sentence and its README, with no pointer to other versions, and opens the README in one click", async () => {
    serve(withoutFiles());
    open();
    const empty = await screen.findByTestId("hub-entry-files-empty");
    expect(
      within(empty).getByText(
        "Version 1.0.0 has no files to show. Read its README.",
      ),
    ).toBeInTheDocument();
    expect(empty).not.toHaveTextContent("another version");
    fireEvent.click(
      within(empty).getByRole("button", { name: "Open the README" }),
    );
    await waitFor(() => expect(currentPath()).toBe(AT));
    expect(await screen.findByTestId("hub-readme")).toHaveTextContent(
      "Run it with your key",
    );
  });

  it("with no README either, ends at its own sentence", async () => {
    serve(withoutFiles({ readme: "" }));
    open();
    const empty = await screen.findByTestId("hub-entry-files-empty");
    expect(
      within(empty).getByText("Version 1.0.0 has no files to show."),
    ).toBeInTheDocument();
    expect(empty).not.toHaveTextContent("another version");
    expect(
      within(empty).queryByRole("button", { name: "Open the README" }),
    ).toBeNull();
  });
});

describe("Files tab of a version with no files, on an entry with more versions", () => {
  it("still points to the Versions table, and names the README button as the file is named", async () => {
    serve(withoutFiles({ versionCount: 2 }));
    open();
    const empty = await screen.findByTestId("hub-entry-files-empty");
    expect(
      within(empty).getByText(
        "Version 1.0.0 has no files to show. Read its README, or open another version from the Versions table.",
      ),
    ).toBeInTheDocument();
    expect(
      within(empty).getByRole("button", { name: "Open the README" }),
    ).toBeInTheDocument();
    expect(
      within(empty).getByRole("button", { name: "See all versions" }),
    ).toBeInTheDocument();
  });
});

describe("Files tab: a file longer than the registry shows", () => {
  it("gives the fixed limit as a whole number: the first 64 KB is shown", async () => {
    const entry = structuredClone(official);
    entry.latest.files = [
      { lang: "javascript", path: "main.mjs", bytes: 150 * 1024 + 512 },
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
      /^This file is 150\.5 KB; the first 64 KB is shown\. Download the archive for the whole file\.$/,
    );
  });
});
