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

// What a visitor does on an entry page in one click, as on a package or an
// image page: copy the bundle's whole digest to check a download against,
// download any listed version from its own row of the Versions table, and
// reach the download beside the words that describe it.

const ENTRY = "/omega-hub/v1/entries/future-agi/first-agent";
const AT = "/dashboard/hub/future-agi/first-agent";

function serve(entry = structuredClone(official), rows = versions) {
  respond([
    ["get", "/api/capabilities/", capabilitiesAnswer({ parts: [] })],
    ["get", ENTRY, entry],
    ["get", `${ENTRY}/versions`, rows],
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

const open = (at = AT) =>
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

const bundleHref = (version) =>
  `http://localhost:8000${ENTRY}/versions/${version}/bundle?source=web`;

beforeEach(() => vi.stubEnv("VITE_OMEGA_CONSOLE", "1"));
afterEach(() => vi.unstubAllEnvs());

describe("Provenance", () => {
  it("copies the bundle's whole digest from beside its short form", async () => {
    const writeText = clipboard();
    serve();
    open();
    const provenance = await screen.findByRole("region", {
      name: "Provenance",
    });
    const { digest } = official.latest;
    expect(provenance).toHaveTextContent("sha256:7465d5950300");
    fireEvent.click(
      within(provenance).getByRole("button", { name: "Copy the whole digest" }),
    );
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(digest));
  });
});

describe("Versions table", () => {
  const rows = [
    { ...versions[0], version: "1.0.0" },
    {
      ...versions[0],
      version: "1.2.0",
      publishedAt: "2026-11-02T09:00:00+00:00",
      status: "yanked",
      yankedReason: "Prints a key in its log.",
    },
    {
      ...versions[0],
      version: "1.10.0",
      publishedAt: "2026-10-20T09:00:00+00:00",
    },
  ];
  const row = (version) =>
    screen
      .getAllByTestId("hub-version-row")
      .find((node) => node.getAttribute("data-version") === version);

  it("each listed version downloads from its own row, in a new tab; a yanked one offers no download", async () => {
    serve(structuredClone(official), rows);
    open(`${AT}/versions`);
    await screen.findByRole("table", { name: "Versions" });
    for (const version of ["1.0.0", "1.10.0"]) {
      const link = within(row(version)).getByRole("link", {
        name: `Download .tar.gz of version ${version}`,
      });
      expect(link).toHaveTextContent(/^Download \.tar\.gz$/);
      expect(link).toHaveAttribute("href", bundleHref(version));
      expect(link).toHaveAttribute("target", "_blank");
      expect(link).toHaveAttribute("rel", "noopener noreferrer");
      // On the bundle's own line, under the notes.
      expect(within(row(version)).getByTestId("hub-version-bundle")).toBe(
        link.closest('[data-testid="hub-version-bundle"]'),
      );
    }
    expect(
      within(row("1.2.0")).queryByRole("link", { name: /^Download/ }),
    ).toBeNull();
    expect(row("1.2.0")).toHaveTextContent("Prints a key in its log.");
  });
});

describe("The title block's download", () => {
  it("sets its words beside its button when the column has room, the button keeping its size", async () => {
    serve();
    open();
    const files = await screen.findByRole("region", { name: "Get the files" });
    const style = getComputedStyle(files);
    expect(style.display).toBe("flex");
    expect(style.flexDirection).toBe("row");
    expect(style.flexWrap).toBe("wrap");
    const button = within(files).getByRole("link", {
      name: "Download (.tar.gz)",
    });
    expect(button).toHaveAttribute("href", bundleHref("1.0.0"));
    expect(getComputedStyle(button).flexShrink).toBe("0");
    expect(files).toHaveTextContent(
      "The approved files of version 1.0.0 in one archive, 4.3 KB.",
    );
  });
});
