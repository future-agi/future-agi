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

// Provenance names two digests, each for what it covers. The files digest is
// the digest of the approved files, not of the archive a visitor downloads.
// The archive digest is the sha256 of that .tar.gz: the value the download
// carries in its X-Omega-Hub-Bundle-Sha256 header (the server's own test pins
// the two equal), so a download can be checked against it. Each row's label
// says on hover what its digest covers, each digest shows in short form with
// its whole value on hover, and each is copied whole by a button of the same
// size beside it.

const ENTRY = "/omega-hub/v1/entries/future-agi/first-agent";
const AT = "/dashboard/hub/future-agi/first-agent";
const FILES_TITLE =
  "The digest of the approved files, not of the downloaded archive.";
const ARCHIVE_TITLE = "The sha256 of the .tar.gz you download.";

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

// `extra` adds routes, such as an older version the page is pinned to.
function serve(entry = structuredClone(official), extra = []) {
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
    ...extra,
  ]);
}

const open = (at = AT) =>
  renderAt(<HubEntryPage />, {
    path: "/dashboard/hub/:publisher/:name/:tab?",
    at,
  });

const provenanceRegion = () =>
  screen.findByRole("region", { name: "Provenance" });

// A row of Provenance's fact list: its label (a term) and its value.
function rowOf(provenance, label) {
  const term = within(provenance).getByText(label);
  expect(term.tagName).toBe("DT");
  const value = term.nextElementSibling;
  expect(value.tagName).toBe("DD");
  return { term, value };
}

// A copy button's target and glyph, as a browser draws them.
function drawn(button) {
  const box = getComputedStyle(button);
  const glyph = getComputedStyle(button.querySelector("svg"));
  return {
    padding: [
      box.paddingTop,
      box.paddingRight,
      box.paddingBottom,
      box.paddingLeft,
    ],
    width: glyph.width,
    height: glyph.height,
  };
}

beforeEach(() => vi.stubEnv("VITE_OMEGA_CONSOLE", "1"));
afterEach(() => vi.unstubAllEnvs());

describe("Provenance: the files digest and the archive digest", () => {
  it("names the approved files' digest Files digest, says on hover that it is not the archive's, and still copies it whole", async () => {
    const writeText = clipboard();
    serve();
    open();
    const provenance = await provenanceRegion();
    expect(within(provenance).queryByText("Bundle")).toBeNull();
    const { term, value } = rowOf(provenance, "Files digest");
    expect(term).toHaveAttribute("title", FILES_TITLE);
    const { digest } = official.latest;
    expect(value).toHaveTextContent("sha256:7465d5950300");
    // The whole digest on hover over its short form, as before.
    expect(within(value).getByTitle(digest)).toHaveTextContent(
      "sha256:7465d5950300",
    );
    fireEvent.click(
      within(value).getByRole("button", { name: "Copy the whole digest" }),
    );
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(digest));
  });

  it("adds an Archive digest row right after it: the sha256 of the .tar.gz you download, copied exactly as the download's header carries it", async () => {
    const writeText = clipboard();
    serve();
    open();
    const provenance = await provenanceRegion();
    const { bundleSha256 } = official.latest;
    const { term, value } = rowOf(provenance, "Archive digest");
    expect(term).toHaveAttribute("title", ARCHIVE_TITLE);
    expect(value).toHaveTextContent(`sha256:${bundleSha256.slice(0, 12)}`);
    expect(within(value).getByTitle(bundleSha256)).toBeInTheDocument();
    fireEvent.click(
      within(value).getByRole("button", { name: "Copy the archive digest" }),
    );
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(bundleSha256));
    // The bare 64 hex characters, as the header carries them: no prefix.
    expect(writeText.mock.calls.at(-1)[0]).toMatch(/^[0-9a-f]{64}$/);
    // Files digest, then Archive digest, then the size, as one group.
    const terms = [...provenance.querySelectorAll("dt")].map(
      (node) => node.textContent,
    );
    const at = terms.indexOf("Files digest");
    expect(terms.slice(at, at + 3)).toEqual([
      "Files digest",
      "Archive digest",
      "Size",
    ]);
  });

  it("draws the archive digest's copy button as the files digest's: the same glyph and the same target", async () => {
    serve();
    open();
    const provenance = await provenanceRegion();
    const files = rowOf(provenance, "Files digest").value;
    const archive = rowOf(provenance, "Archive digest").value;
    expect(drawn(within(archive).getByRole("button"))).toEqual(
      drawn(within(files).getByRole("button")),
    );
  });

  it("on a page pinned to another version, shows and copies that version's archive digest", async () => {
    const writeText = clipboard();
    const older = {
      ...structuredClone(official.latest),
      version: "0.9.0",
      bundleSha256: "0123456789abcdef".repeat(4),
    };
    serve(structuredClone(official), [
      ["get", `${ENTRY}/versions/0.9.0`, older],
    ]);
    open(`${AT}?version=0.9.0`);
    const provenance = await provenanceRegion();
    const { value } = rowOf(provenance, "Archive digest");
    expect(value).toHaveTextContent("sha256:0123456789ab");
    fireEvent.click(
      within(value).getByRole("button", { name: "Copy the archive digest" }),
    );
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith(older.bundleSha256),
    );
  });

  it("for a version sent without an archive digest, shows the files digest alone and no empty row", async () => {
    const entry = structuredClone(official);
    entry.latest.bundleSha256 = null;
    serve(entry);
    open();
    const provenance = await provenanceRegion();
    expect(rowOf(provenance, "Files digest").value).toHaveTextContent(
      "sha256:7465d5950300",
    );
    expect(within(provenance).queryByText("Archive digest")).toBeNull();
    expect(
      within(provenance).queryByRole("button", {
        name: "Copy the archive digest",
      }),
    ).toBeNull();
  });
});
