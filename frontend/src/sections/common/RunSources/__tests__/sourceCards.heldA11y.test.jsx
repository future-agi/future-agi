// Accessibility of the held ways to run with the real tooltip (every other
// picker test mocks it): a screen reader hears each greyed card or tile by
// its own name, hears "Not available in this release" as its description,
// and cannot start anything from it with the keyboard. A greyed card is out
// of the tab order; a greyed tile stays in it, as every greyed tile does, so
// its reason can be read. MUI's Tooltip names its child by a text title
// unless the child names itself, so this is checked with the real one.
import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "src/utils/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import SourceCards from "../SourceCards";
import CatalogTile from "../CatalogTile";
import { heldEntriesFor } from "../catalog";
import { availabilityOfReport } from "../runSourceAvailability";

vi.mock("src/launch/launchSwitch", async (importOriginal) => ({
  ...(await importOriginal()),
  isSurfaceShown: () => true,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../BundledIcon", () => ({ default: () => null }));
vi.mock("../SourceLogo", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock(
  "src/sections/common/Connect/ConnectionPill",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => null,
  }),
);
vi.mock("src/api/develop/prompt", async (importOriginal) => ({
  ...(await importOriginal()),
  usePromptExecutions: () => ({ data: { pages: [] }, isPending: false }),
}));
vi.mock("src/api/experiment/use-get-agents", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetListOfAgents: () => ({ data: { pages: [] }, isPending: false }),
}));
vi.mock("../graphModels-g2", async (importOriginal) => ({
  ...(await importOriginal()),
  useGraphModels: () => ({ data: {}, isLoading: false }),
  withGraphModels: (graphs) => graphs,
}));

const LINE = "Not available in this release";
const YES = { available: true, reason: null };
const READY = availabilityOfReport({
  workspace_id: "00000000-0000-4000-8000-000000000001",
  sources: {
    prompt: YES,
    agent: YES,
    http: YES,
    a2a: {
      available: false,
      reason: "A2A experiments are not built in this release.",
    },
    mcp_tool: {
      available: false,
      reason: "MCP tool experiments are not built in this release.",
    },
  },
});

afterEach(cleanup);

describe("held ways to run, with the real tooltip", () => {
  it.each(["Your code (SDK / CLI)", "Agent harness", "Voice agent"])(
    "step 1 names %s by its title and describes it as not in this release",
    (title) => {
      const onPick = vi.fn();
      render(
        <SourceCards
          datasetKind="rows"
          query=""
          onQuery={vi.fn()}
          onPickCard={onPick}
          onPickEntry={onPick}
          availability={READY}
        />,
      );
      const card = screen.getByRole("button", { name: title });
      expect(card).toHaveAttribute("aria-disabled", "true");
      expect(card).toHaveAttribute("tabindex", "-1");
      expect(card).toHaveAccessibleDescription(LINE);
      expect(within(card).getByText(LINE)).toBeVisible();
      fireEvent.click(card);
      fireEvent.keyDown(card, { key: "Enter" });
      fireEvent.keyDown(card, { key: " " });
      expect(onPick).not.toHaveBeenCalled();
    },
  );

  it.each([
    ["traces", "Traces I already have"],
    ["upload", "Upload results"],
  ])(
    "the Already running list names %s and describes it as not in this release",
    (id, name) => {
      const onPick = vi.fn();
      render(
        <CatalogTile
          entry={heldEntriesFor("running").find((e) => e.id === id)}
          onPick={onPick}
          availability={READY}
        />,
      );
      const tile = screen.getByRole("button", { name });
      expect(tile).toHaveAttribute("aria-disabled", "true");
      expect(tile).toHaveAttribute("tabindex", "0");
      expect(tile).toHaveAccessibleDescription(LINE);
      fireEvent.click(tile);
      fireEvent.keyDown(tile, { key: "Enter" });
      fireEvent.keyDown(tile, { key: " " });
      expect(onPick).not.toHaveBeenCalled();
    },
  );
});
