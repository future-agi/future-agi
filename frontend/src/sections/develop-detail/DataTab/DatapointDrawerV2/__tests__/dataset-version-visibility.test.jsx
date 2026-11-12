import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import {
  isSurfaceShown,
  setLaunchOverrideForTest,
} from "src/launch/launchSwitch";

const { Null } = vi.hoisted(() => ({ Null: () => null }));
vi.mock("src/components/iconify", () => ({ default: Null }));
vi.mock("src/sections/common/Datasets/datasetKind", () => ({
  useDatasetKind: () => ({ kind: null, dataset: null }),
}));
vi.mock("src/api/develop/dataset-versions", () => ({
  useDatasetVersions: () => ({ data: [] }),
}));
vi.mock("src/sections/common/Collab/RowCollabPills", () => ({ default: Null }));
vi.mock("../../Splits/AssignSplitsMenu", () => ({ default: Null }));
vi.mock("src/sections/common/Datasets/SplitChips", () => ({ default: Null }));

import RowMoreMenu from "../RowMoreMenu";
import { RowHeaderLine2 } from "../RowViewHeader";
import { RowNotFound } from "../RowViewStates";
import { versionPillTooltip } from "../rowModel";

const theme = createTheme({ palette: palette("light") });
const meta = { added_in_version: 1, last_changed_in_version: 4 };
const priorTooltip =
  "Added in v1. Last edited in v4. Click for the version history.";
const profiles = [
  ["launch", { launch: true, experimentsBasic: true }, false],
  ["prototype", { launch: false }, true],
  [
    "explicitly shown",
    { launch: true, experimentsBasic: true, madeReal: ["dataset.versions"] },
    true,
  ],
];
const themed = (children) =>
  render(<ThemeProvider theme={theme}>{children}</ThemeProvider>);

afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
});

describe.each(profiles)("dataset version visibility: %s", (_name, state, shown) => {
  it("keeps the prior menu items and action only when versions are shown", () => {
    setLaunchOverrideForTest(state);
    expect(isSurfaceShown("dataset.versions")).toBe(shown);
    const history = vi.fn();
    themed(
      <RowMoreMenu
        canEdit
        items={{
          copyJson: { onClick: vi.fn() },
          download: { onClick: vi.fn() },
          history: { onClick: history },
        }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "More actions" }));
    expect(screen.getAllByRole("menuitem").map((el) => el.textContent)).toEqual(
      shown
        ? ["Copy row as JSON", "Download row (JSON)", "Open version history"]
        : ["Copy row as JSON", "Download row (JSON)"],
    );
    if (shown) {
      fireEvent.click(screen.getByRole("menuitem", { name: "Open version history" }));
      expect(history).toHaveBeenCalledTimes(1);
    } else {
      expect(screen.queryByText(/version|history/i)).toBeNull();
      expect(history).not.toHaveBeenCalled();
    }
  });

  it("hides the entire metadata-backed pill and tooltip, not just its click handler", async () => {
    setLaunchOverrideForTest(state);
    const onOpenHistory = vi.fn();
    themed(
      <RowHeaderLine2
        datasetId="ds-1"
        row={{ row_id: "R1", row_meta: meta }}
        splits={[]}
        canEdit={false}
        onOpenHistory={shown ? onOpenHistory : undefined}
      />,
    );
    expect(versionPillTooltip(meta, [])).toBe(shown ? priorTooltip : "");
    const pill = screen.queryByTestId("row-version-pill");
    if (!shown) {
      expect(pill).toBeNull();
      expect(screen.queryByRole("tooltip")).toBeNull();
      return;
    }
    expect(pill.textContent).toBe("Added v1 · edited v4");
    fireEvent.mouseOver(pill);
    expect((await screen.findByRole("tooltip")).textContent).toBe(priorTooltip);
    fireEvent.click(pill);
    expect(onOpenHistory).toHaveBeenCalledTimes(1);
  });

  it("preserves the existing historyDoor protection for a missing row", () => {
    setLaunchOverrideForTest(state);
    themed(
      <RowNotFound
        onClose={vi.fn()}
        onOpenHistory={isSurfaceShown("dataset.versions") ? vi.fn() : undefined}
      />,
    );
    if (shown) {
      expect(screen.getByRole("button", { name: "View version history" })).toBeTruthy();
      expect(screen.getByText(/The version history lists every deleted row\./)).toBeTruthy();
    } else {
      expect(screen.queryByText(/version|history/i)).toBeNull();
    }
  });
});
