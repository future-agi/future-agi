import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/api/develop/dataset-splits", () => ({
  useDatasetSplits: () => ({ data: { splits: [] }, isPending: false }),
  useUpdateSplit: () => ({ mutate: vi.fn() }),
  useDeleteSplit: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCreateSplit: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ refreshGrid: vi.fn() }),
}));
vi.mock("../TrainingSetDialog", () => ({ TrainingSetButton: () => null }));
vi.mock("../AssignSplitsMenu", () => ({ SPLIT_NAME_RE: /^[a-z]+$/ }));

import ManageSplitsDialog from "../ManageSplitsDialog";
import { useManageSplitsStore } from "../splitStores";

const theme = createTheme({ palette: palette("light") });
afterEach(() => {
  cleanup();
  useManageSplitsStore.setState({ open: false });
  setLaunchOverrideForTest(null);
});

describe.each([
  ["launch with shipped splits", true],
  ["prototype", false],
])("Manage splits copy: %s", (_name, launch) => {
  it("uses the exact sealed paragraph on both profiles without hiding splits", () => {
    setLaunchOverrideForTest({ launch, experimentsBasic: true });
    useManageSplitsStore.setState({ open: true });
    render(<ThemeProvider theme={theme}><ManageSplitsDialog datasetId="ds-1" /></ThemeProvider>);
    const sealedCopy = "Splits are named subsets of current rows. A row can be in several. Changes are saved immediately.";
    const paragraph = screen.getByText(sealedCopy, { exact: true });
    expect(paragraph.textContent).toBe(sealedCopy);
    expect(screen.getByRole("dialog").textContent).not.toMatch(/version|history/i);
    expect(screen.getByRole("button", { name: "Create split" })).toBeTruthy();
  });
});
