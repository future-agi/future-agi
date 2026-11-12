// `X has not run on this dataset. [Add an X run]` offers only a source the
// server's run-source report says this workspace runs
// (GET /api/capabilities/ experiment_run_sources); until it answers, none.
import React from "react";
import { cleanup, fireEvent, render, screen } from "src/utils/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  FAILED_AVAILABILITY,
  LOADING_AVAILABILITY,
  availabilityOfReport,
} from "src/sections/common/RunSources/runSourceAvailability";
import { catalogEntryNamed, notRunHere } from "../notRunHere";
import RunPickerPopover from "../RunPickerPopover";

const harness = vi.hoisted(() => ({ availability: null }));
vi.mock("src/launch/launchSwitch", async (importOriginal) => ({
  ...(await importOriginal()),
  isSurfaceShown: () => true,
}));
vi.mock("src/sections/common/RunSources/useRunSourceAvailability", () => ({
  useRunSourceAvailability: () => harness.availability,
}));
// The dataset has no runs, so every search ends on the empty line.
vi.mock("src/api/develop/runs", async (importOriginal) => ({
  ...(await importOriginal()),
  useDatasetRuns: () => ({
    data: { results: [], baseline: null },
    isLoading: false,
    isError: false,
    refetch: () => {},
  }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
/* eslint-disable react/prop-types */
vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));
/* eslint-enable react/prop-types */

const YES = { available: true, reason: null };
const NO = (reason) => ({ available: false, reason });
const ready = (sources = {}) =>
  availabilityOfReport({
    workspace_id: "00000000-0000-4000-8000-000000000001",
    sources: {
      prompt: YES,
      agent: YES,
      http: YES,
      a2a: NO("A2A experiments are not built in this release."),
      mcp_tool: NO("MCP tool experiments are not built in this release."),
      ...sources,
    },
  });
const HTTP_ONLY = ready();

beforeEach(() => {
  harness.availability = HTTP_ONLY;
});
afterEach(cleanup);

describe("notRunHere follows the server's report", () => {
  it.each([
    ["mcp", "mcp_tool", "MCP server tool"],
    ["a2a", "a2a", "A2A agent card"],
    ["MCP server tool", "mcp_tool", "MCP server tool"],
  ])(
    "still names %s, with the server's reason and no live add",
    (q, id, name) => {
      const miss = notRunHere(q, { datasetId: "d", availability: HTTP_ONLY });
      expect(miss).toMatchObject({ entry: { id, name } });
      expect(miss.unavailableReason).toBe(HTTP_ONLY.sources[id].reason);
      // Offers (catalogEntryNamed) still name only what runs.
      expect(catalogEntryNamed(q, HTTP_ONLY)).toBeNull();
    },
  );

  it("names the HTTP endpoint the server runs", () => {
    expect(
      notRunHere("http", { datasetId: "d", availability: HTTP_ONLY }),
    ).toEqual({
      entry: { id: "http", name: "HTTP endpoint" },
      text: "HTTP endpoint has not run on this dataset.",
      action: "Add an HTTP endpoint run",
      href: "/dashboard/develop/d?tab=experiments&new_experiment=1&entry=http",
      unavailableReason: null,
    });
  });

  it.each([
    ["loading", LOADING_AVAILABILITY],
    ["failed", FAILED_AVAILABILITY],
    ["unavailable", ready({ http: NO("Not on this server.") })],
    ["not passed", undefined],
  ])(
    "names the endpoint with a disabled add while the report is %s",
    (state, availability) => {
      const miss = notRunHere("http", { datasetId: "d", availability });
      expect(miss).toMatchObject({
        text: "HTTP endpoint has not run on this dataset.",
        action: "Add an HTTP endpoint run",
      });
      expect(miss.unavailableReason).toBe(
        state === "unavailable"
          ? "Not on this server."
          : "Not available in this deployment",
      );
    },
  );

  it("names an MCP tool run once the server runs it", () => {
    const a = ready({ mcp_tool: YES });
    expect(
      notRunHere("mcp", { datasetId: "d", availability: a }),
    ).toMatchObject({
      action: "Add an MCP server tool run",
      href: "/dashboard/develop/d?tab=experiments&new_experiment=1&entry=mcp_tool",
    });
  });
});

describe("the compare-runs picker adds only a source the server runs", () => {
  const searchFor = (q) => {
    render(<RunPickerPopover datasetId="d" onPick={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Add runs" }));
    fireEvent.change(screen.getByPlaceholderText("Search #4, name, model"), {
      target: { value: q },
    });
  };

  it.each([
    ["mcp", "MCP server tool", "Add an MCP server tool run"],
    ["a2a", "A2A agent card", "Add an A2A agent card run"],
  ])("shows %s with its reason and a disabled add", (q, name, action) => {
    searchFor(q);
    const miss = screen.getByTestId("picker-not-run-here");
    expect(miss).toHaveTextContent(`${name} has not run on this dataset.`);
    expect(screen.getByRole("button", { name: action })).toBeDisabled();
    expect(screen.queryByRole("link", { name: action })).toBeNull();
  });

  it("links to the HTTP endpoint run the server runs", () => {
    searchFor("http");
    const miss = screen.getByTestId("picker-not-run-here");
    expect(miss).toHaveTextContent(
      "HTTP endpoint has not run on this dataset.",
    );
    expect(
      screen.getByRole("link", { name: "Add an HTTP endpoint run" }),
    ).toHaveAttribute(
      "href",
      "/dashboard/develop/d?tab=experiments&new_experiment=1&entry=http",
    );
  });

  it("keeps the endpoint line with a disabled add while the report loads", () => {
    harness.availability = LOADING_AVAILABILITY;
    searchFor("http");
    expect(screen.getByTestId("picker-not-run-here")).toHaveTextContent(
      "Not available in this deployment",
    );
    expect(
      screen.getByRole("button", { name: "Add an HTTP endpoint run" }),
    ).toBeDisabled();
  });
});
