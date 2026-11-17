// IT-R3 G1 P1: the run tray never covers an open drawer or dialog; a
// tracked run whose experiment is gone leaves with one neutral toast.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import PropTypes from "prop-types";
import { Button, Dialog, Drawer, Stack } from "@mui/material";

const detail = { data: undefined, error: null };
vi.mock("src/api/develop/runs", () => ({
  useExperimentDetail: () => detail,
}));
const notify = vi.fn();
vi.mock("src/sections/common/ux/toast", () => ({
  notify: (...a) => notify(...a),
}));

const { default: RunTray, RunTrayDock } = await import("../RunTray");
const { useRunTray, removedRunsText, isMissingExperimentError, trayLabel } =
  await import("../runTrayStore");

const RUN = {
  run_id: "a6000000-0000-4000-8000-00000000004a",
  experiment_id: "e6000000-0000-4000-8000-000000000004",
  label: "#4A",
  name: "Claude Code",
  status: "waiting_for_results",
};
const ENTRY = {
  runId: RUN.run_id,
  experimentId: RUN.experiment_id,
  datasetId: "d5a1e7c2-0b3f-4a8d-9e61-1c2b3a4d5e06",
  label: "#4A",
  addedAt: 1,
};

function Wizard({ open, dialog }) {
  return (
    <>
      <Drawer anchor="right" open={open} sx={{ zIndex: 1099 }}>
        <Stack direction="row" data-testid="drawer-header">
          <RunTrayDock />
        </Stack>
        <Stack direction="row" data-testid="drawer-footer">
          <Button variant="outlined">Try on 1 row</Button>
          <Button variant="contained">Add to experiment</Button>
        </Stack>
      </Drawer>
      <Dialog open={Boolean(dialog)}>Add MCP server</Dialog>
    </>
  );
}

Wizard.propTypes = { open: PropTypes.bool, dialog: PropTypes.bool };

const renderAll = (props) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <RunTray />
        <Wizard {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  );

beforeEach(() => {
  detail.data = { runs: [RUN] };
  detail.error = null;
  notify.mockReset();
  useRunTray.setState({ runs: [ENTRY], live: {} });
});
afterEach(() => {
  cleanup();
  useRunTray.setState({ runs: [], live: {} });
});

describe("run tray layers (P1)", () => {
  it("shows the pill when nothing is open", async () => {
    renderAll({ open: false });
    await act(async () => {});
    expect(screen.getByTestId("run-tray").textContent).toBe(
      "1 run waiting for your terminal · #4A",
    );
  });

  it("hides the pill while the New experiment drawer is open and docks a chip in its header", async () => {
    renderAll({ open: true });
    await act(async () => {});
    expect(screen.queryByTestId("run-tray")).toBeNull();
    const dock = screen.getByTestId("run-tray-dock");
    expect(dock.textContent).toBe("1 run waiting · #4A");
    expect(screen.getByTestId("drawer-header").contains(dock)).toBe(true);
    // Nothing of the tray is left in the footer's way.
    expect(screen.getByText("Add to experiment")).toBeTruthy();
    expect(screen.getByText("Try on 1 row")).toBeTruthy();
    expect(document.querySelectorAll('[data-testid="run-tray"]')).toHaveLength(
      0,
    );
  });

  it("hides the pill while a dialog (Add MCP server) is open", async () => {
    renderAll({ open: false, dialog: true });
    await act(async () => {});
    expect(screen.queryByTestId("run-tray")).toBeNull();
  });

  it("drops a run whose experiment is gone, with one neutral toast", async () => {
    detail.data = undefined;
    detail.error = {
      status: false,
      result: "Experiment not found",
      statusCode: 404,
    };
    renderAll({ open: false });
    await act(async () => {});
    expect(useRunTray.getState().runs).toHaveLength(0);
    expect(notify).toHaveBeenCalledTimes(1);
    const [msg, opts] = notify.mock.calls[0];
    expect(msg).toBe("Run #4A was removed");
    expect(opts.variant).toBe("default");
    expect(screen.queryByTestId("run-tray")).toBeNull();
  });
});

describe("run tray helpers", () => {
  it("reads a 404 or a not-found result as missing", () => {
    expect(isMissingExperimentError({ statusCode: 404 })).toBe(true);
    expect(
      isMissingExperimentError({
        statusCode: 400,
        result: "Experiment not found",
      }),
    ).toBe(true);
    expect(isMissingExperimentError({ statusCode: 500, result: "boom" })).toBe(
      false,
    );
    expect(isMissingExperimentError(null)).toBe(false);
  });
  it("names removed runs", () => {
    expect(removedRunsText(["#4A"])).toBe("Run #4A was removed");
    expect(removedRunsText(["#4A", "#4B"])).toBe("Runs #4A, #4B were removed");
  });
  it("summarises short and long", () => {
    const w = () => true;
    expect(trayLabel([ENTRY], { [RUN.run_id]: RUN }, w, { short: true })).toBe(
      "1 run waiting · #4A",
    );
    expect(trayLabel([ENTRY], {}, w, { short: true })).toBe(
      "1 run · Checking…",
    );
  });
});
