/* eslint-disable react/prop-types */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import {
  experimentsBasicFromEnv,
  isSurfaceShown,
  setLaunchOverrideForTest,
} from "../launchSwitch";
import { madeRealSurfaceIds } from "../madeReal";

const open = vi.hoisted(() => vi.fn());
vi.mock("src/auth/hooks", () => ({
  // Match the app's case-sensitive RolePermission key, not an unknown role.
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/utils/Mixpanel", () => ({ trackEvent: vi.fn(), Events: {} }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/sections/common/Access/useDatasetAccess", () => ({
  default: () => ({ can: { run: true }, reason: () => null }),
}));
vi.mock("src/sections/develop-detail/states", () => ({
  useRunExperimentStoreShallow: (select) =>
    select({ initiateCreateMode: open }),
}));
vi.mock(
  "src/sections/develop-detail/DataTab/Versions/PendingChangesGate",
  () => ({
    usePendingChangesGate: () => (_action, callback) => callback(),
  }),
);
vi.mock("src/sections/common/Datasets/datasetKind", () => ({
  useDatasetKind: () => ({ kind: "rows", dataset: { kind: "rows" } }),
}));
vi.mock("src/sections/develop-detail/ExperimentTab/useOpenSource", () => ({
  default: () => open,
}));
vi.mock("src/api/develop/prompt", () => ({
  usePromptExecutions: () => ({ data: { pages: [] }, isPending: false }),
}));
vi.mock("src/api/experiment/use-get-agents", () => ({
  useGetListOfAgents: () => ({ data: { pages: [] }, isPending: false }),
}));
vi.mock("src/sections/common/RunSources/graphModels-g2", () => ({
  useGraphModels: () => ({ data: {}, isLoading: false }),
  withGraphModels: (graphs) => graphs,
}));

import DevelopBar from "src/sections/develop-detail/DevelopBar/DevelopBar";
import NewExperimentButton from "src/sections/develop-detail/Experiment/NewExperimentButton";
import { RowMenuList } from "src/sections/develop/DatasetListCells";
import SourceCards from "src/sections/common/RunSources/SourceCards";
import CreateAdmissionNotice from "src/sections/develop-detail/Experiment/CreateAdmissionNotice";
import { recoveryKey } from "src/sections/develop-detail/Experiment/createAdmission";

const theme = createTheme({ palette: palette("light") });
const clients = [];
const LocationProbe = () => {
  const { pathname, search } = useLocation();
  return (
    <output data-testid="location">
      {pathname}
      {search}
    </output>
  );
};
function mount(ui, seed = () => {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  seed(client);
  return render(
    <ThemeProvider theme={theme}>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/dashboard/develop/d1"]}>
          <LocationProbe />
          <Routes>
            <Route path="/dashboard/develop/:dataset" element={ui} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

beforeEach(() => {
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: experimentsBasicFromEnv({
      VITE_DSX_EXPERIMENTS_BASIC: "false",
    }),
    madeReal: madeRealSurfaceIds(),
  });
  open.mockClear();
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  setLaunchOverrideForTest(null);
});

describe("J09 classic experiment entry controls with an explicit false build input", () => {
  it("disables NEW enhancements without hiding the classic creation host", () => {
    expect(
      experimentsBasicFromEnv({ VITE_DSX_EXPERIMENTS_BASIC: "false" }),
    ).toBe(false);
    expect(isSurfaceShown("exp.new")).toBe(false);
    expect(isSurfaceShown("exp.classic")).toBe(true);
    mount(<NewExperimentButton placement="header" />);
    const button = screen.getByRole("button", { name: "New experiment" });
    expect(button).toBeVisible();
    expect(button).toBeEnabled();
    fireEvent.click(button);
    expect(open).toHaveBeenCalledWith({
      prefill: undefined,
      firstRunType: undefined,
    });
    expect(screen.queryByTestId("new-experiment-kinds")).toBeNull();
  });

  it("keeps the NEW durable-recovery notice hidden even with a saved submission", () => {
    mount(<CreateAdmissionNotice datasetId="d1" />, (client) => {
      client.setQueryData(recoveryKey("d1"), { name: "Saved prompt" });
    });
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText(/Execution admission is unconfirmed/)).toBeNull();
  });

  it("renders enabled classic Experiments and Summary tabs and activates them", () => {
    const setCurrentTab = vi.fn();
    mount(
      <DevelopBar
        currentTab="data"
        setCurrentTab={setCurrentTab}
        rowSelected={[]}
        setRowSelected={vi.fn()}
        tabRef={{ current: null }}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "Experiments" }));
    expect(setCurrentTab).toHaveBeenCalledWith("experiments");
    // Census §A restores the classic Summary host; only its NEW parts stay off.
    const summary = screen.getByRole("tab", { name: "Summary" });
    expect(summary).toBeVisible();
    expect(summary).toBeEnabled();
    fireEvent.click(summary);
    expect(setCurrentTab).toHaveBeenLastCalledWith("summary");
  });

  it.each(["header", "experiments"])(
    "opens prompt creation from the %s button, without a kinds menu",
    (placement) => {
      mount(<NewExperimentButton placement={placement} />);
      const button = screen.getByRole("button", { name: "New experiment" });
      expect(button).toBeEnabled();
      fireEvent.click(button);
      expect(open).toHaveBeenCalledWith({
        prefill: undefined,
        firstRunType: undefined,
      });
      expect(screen.queryByTestId("new-experiment-kinds")).toBeNull();
      expect(screen.queryByRole("menu")).toBeNull();
    },
  );

  it.each([
    ["Open experiments", "?tab=experiments"],
    ["New experiment…", "?tab=data&new_experiment=1"],
  ])("keeps the list-row %s action wired", (label, query) => {
    mount(
      <RowMenuList
        open
        anchorReference="anchorPosition"
        anchorPosition={{ top: 0, left: 0 }}
        row={{ id: "d1" }}
        actions={{ open: vi.fn() }}
        canEdit
        canDelete
        onClose={vi.fn()}
      />,
    );
    const action = screen.getByRole("menuitem", { name: label });
    expect(action).not.toHaveAttribute("aria-disabled", "true");
    fireEvent.click(action);
    expect(screen.getByTestId("location").textContent).toBe(
      `/dashboard/develop/d1${query}`,
    );
  });

  it("keeps Agent graph visible but disabled, with a not-available notice", () => {
    const onPick = vi.fn();
    mount(
      <SourceCards
        datasetKind="rows"
        query=""
        onQuery={vi.fn()}
        onPickCard={onPick}
        onPickEntry={onPick}
      />,
    );
    const agent = screen.getByRole("button", { name: "Agent graph" });
    expect(agent).toBeDisabled();
    expect(
      screen.getByText(
        "Agent graph experiments are not available in this release.",
      ),
    ).toBeVisible();
    fireEvent.click(agent);
    expect(onPick).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("source-card-prompt"));
    expect(onPick).toHaveBeenCalledWith("prompt", expect.anything());
    expect(screen.queryByTestId("source-card-running")).toBeNull();
  });
});
