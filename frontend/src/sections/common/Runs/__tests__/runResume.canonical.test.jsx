import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import RunRowMenuHost from "src/sections/develop-detail/ExperimentTab/RunRowMenuHost";
import RunStatusChip from "../RunStatusChip";
import { canResumeRun, stopLimitOf } from "../runResume";

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/sections/develop-detail/ExperimentTab/cellContext", () => ({
  useCell: (props) => props,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/api/develop/runs", async (original) => ({
  ...(await original()),
  useExperimentDetail: () => ({ data: { runs: [] } }),
}));

const stopped = {
  run_id: "run-one",
  experiment_id: "experiment-one",
  label: "#1A",
  status: "cancelled",
  completed_at: null,
  target_type: "prompt",
  progress: { done: 1, total: 3, errors: 0 },
  ledger: { stopped_by: "stopped" },
};
const clients = [];
function mount(node) {
  const client = new QueryClient();
  clients.push(client);
  return render(
    <ThemeProvider theme={createTheme({ palette: palette("light") })}>
      <QueryClientProvider client={client}>
        <MemoryRouter>{node}</MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});

describe("canonical stopped runs", () => {
  it.each(["cancelled", "Cancelled", "CANCELLED", "stopped", "canceled"])(
    "preserves resume and limit rules for %s",
    (status) => {
      const run = { ...stopped, status };
      expect(canResumeRun(run)).toBe(true);
      expect(stopLimitOf(run)).toBe("stopped");
      const limited = { ...run, ledger: { stopped_by: "cost_cap" } };
      expect(canResumeRun(limited)).toBe(false);
      expect(stopLimitOf(limited)).toBe("cost_cap");
      expect(canResumeRun({ ...run, progress: { done: 3, total: 3 } })).toBe(
        false,
      );
    },
  );

  it("renders the chip resume action for the server spelling", () => {
    const view = mount(
      <RunStatusChip run={stopped} actions={{ onResume: vi.fn() }} />,
    );
    expect(view.container.querySelectorAll("button").length).toBe(1);
  });

  it("omits the chip resume action for a limited run", () => {
    const view = mount(
      <RunStatusChip
        run={{ ...stopped, ledger: { stopped_by: "cost_cap" } }}
        actions={{ onResume: vi.fn() }}
      />,
    );
    expect(view.container.querySelectorAll("button").length).toBe(0);
    expect(screen.getByText("Stopped at cost cap").textContent).toBe(
      "Stopped at cost cap",
    );
  });

  it.each([false, true])("menu Resume respects a limit=%s", (limited) => {
    const run = {
      ...stopped,
      ledger: { stopped_by: limited ? "cost_cap" : "stopped" },
    };
    mount(
      <RunRowMenuHost
        menu={{
          group: false,
          experimentId: run.experiment_id,
          runIds: [run.run_id],
          position: { top: 20, left: 20 },
        }}
        rows={[run]}
        context={{
          datasetId: "dataset-one",
          runsPerExperiment: { [run.experiment_id]: 1 },
          allRuns: [run],
          canEdit: true,
          canRun: true,
          canDelete: true,
        }}
        onClose={vi.fn()}
      />,
    );
    expect(
      screen.queryAllByRole("menuitem", { name: "Resume · 2 left" }).length,
    ).toBe(limited ? 0 : 1);
  });
});
