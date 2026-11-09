import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import {
  EXPERIMENTS_BASIC_DEFAULT,
  experimentsBasicFromEnv,
  isSurfaceShown,
  setLaunchOverrideForTest,
} from "src/launch/launchSwitch";

const { details, notify } = vi.hoisted(() => ({
  details: new Map(),
  notify: vi.fn(),
}));
vi.mock("src/api/develop/runs", () => ({
  useExperimentDetail: (id) => details.get(id),
}));
vi.mock("src/sections/common/ux/toast", () => ({ notify }));

const { default: RunTray, RunTrayDock } = await import("../RunTray");
const { RUN_TRAY_KEY, trayLabel, useRunTray } = await import("../runTrayStore");
const RUN = {
  run_id: "run-retained",
  experiment_id: "experiment-retained",
  label: "#4A",
  status: "Running",
};
const ENTRY = {
  runId: RUN.run_id,
  experimentId: RUN.experiment_id,
  datasetId: "dataset-retained",
  label: RUN.label,
};
const detail = () => details.get(ENTRY.experimentId);
const destination =
  `/dashboard/develop/experiment/${ENTRY.experimentId}/data` +
  `?datasetId=${ENTRY.datasetId}`;

function LocationProbe() {
  const location = useLocation();
  return (
    <span data-testid="location">
      {location.pathname}
      {location.search}
    </span>
  );
}

const view = () => (
  <MemoryRouter initialEntries={["/dashboard/develop"]}>
    <RunTray />
    <RunTrayDock />
    <LocationProbe />
  </MemoryRouter>
);
const openTray = () => fireEvent.click(screen.getByTestId("run-tray"));

beforeEach(() => {
  details.clear();
  details.set(ENTRY.experimentId, {
    data: undefined,
    error: null,
    isFetching: false,
    refetch: vi.fn().mockResolvedValue({}),
  });
  notify.mockReset();
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: EXPERIMENTS_BASIC_DEFAULT,
  });
  // Same store shape as the per-tab entries restored on dashboard mount.
  useRunTray.setState({ runs: [ENTRY], live: {}, checks: {} });
});
afterEach(() => {
  cleanup();
  useRunTray.setState({ runs: [], live: {}, checks: {} });
  window.sessionStorage.removeItem(RUN_TRAY_KEY);
  setLaunchOverrideForTest(null);
});

describe("retained run tray at launch defaults", () => {
  it("offers the classic Data destination by default without admitting DSX Results", () => {
    expect(EXPERIMENTS_BASIC_DEFAULT).toBe(true);
    expect(experimentsBasicFromEnv({})).toBe(true);
    expect(isSurfaceShown("exp.page")).toBe(true);
    for (const id of [
      "exp.results.matrix",
      "exp.results.paths",
      "exp.results.summary",
      "exp.results.traces",
    ])
      expect(isSurfaceShown(id), id).toBe(false);
    detail().data = { runs: [RUN] };
    render(view());
    openTray();
    expect(screen.getByTestId("run-tray-dismiss")).toBeTruthy();
    expect(useRunTray.getState().runs).toEqual([ENTRY]);
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    expect(screen.getByTestId("location").textContent).toBe(destination);
  });

  it("keeps status and dismissal but no tray Open in an explicit false build", () => {
    const experimentsBasic = experimentsBasicFromEnv({
      VITE_DSX_EXPERIMENTS_BASIC: "false",
    });
    expect(experimentsBasic).toBe(false);
    setLaunchOverrideForTest({ launch: true, experimentsBasic });
    expect(isSurfaceShown("exp.page")).toBe(false);
    // Classic routes remain admitted; only this palette/tray gate is OFF.
    expect(isSurfaceShown("exp.classic")).toBe(true);
    detail().data = { runs: [RUN] };
    render(view());
    openTray();
    expect(screen.queryByRole("button", { name: "Open" })).toBeNull();
    expect(screen.getByTestId("run-tray-dismiss")).toBeTruthy();
    expect(useRunTray.getState().runs).toEqual([ENTRY]);
    expect(screen.getByTestId("location").textContent).toBe(
      "/dashboard/develop",
    );
  });

  it("does not render a tray in a fresh tab with no retained entries", () => {
    useRunTray.setState({ runs: [] });
    render(view());
    expect(screen.queryByTestId("run-tray")).toBeNull();
    expect(screen.queryByTestId("run-tray-dock")).toBeNull();
  });

  it.each([
    { launch: true, experimentsBasic: true },
    { launch: true, experimentsBasic: false, madeReal: ["exp.page"] },
  ])("preserves Open when the router allows exp.page: %j", (state) => {
    setLaunchOverrideForTest(state);
    detail().data = { runs: [RUN] };
    render(view());
    openTray();
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    expect(screen.getByTestId("location").textContent).toBe(destination);
  });

  it.each(["false", "true"])(
    "gates completion-toast Open with the same classic destination predicate (%s)",
    (value) => {
      const experimentsBasic = experimentsBasicFromEnv({
        VITE_DSX_EXPERIMENTS_BASIC: value,
      });
      setLaunchOverrideForTest({ launch: true, experimentsBasic });
      detail().data = { runs: [{ ...RUN, status: "Completed" }] };
      render(view());
      expect(notify).toHaveBeenCalledTimes(1);
      const [message, options] = notify.mock.calls[0];
      expect(message).toBe("#4A finished · Completed");
      expect(options.variant).toBe("success");
      expect(useRunTray.getState().runs).toHaveLength(0);
      if (experimentsBasic) {
        render(options.action());
        fireEvent.click(screen.getByRole("button", { name: "Open" }));
        expect(screen.getByTestId("location").textContent).toBe(destination);
      } else {
        expect(options.action).toBeUndefined();
      }
    },
  );
});

describe("run status reads", () => {
  it("does not label an unknown status as a run going", () => {
    detail().isFetching = true;
    render(view());
    expect(screen.getByTestId("run-tray").textContent).toBe(
      "1 run · Checking…",
    );
    expect(screen.getByTestId("run-tray-dock").textContent).toBe(
      "1 run · Checking…",
    );
    openTray();
    expect(screen.getByText("Checking…")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it.each([
    { kind: "network", error: new Error("Network unavailable") },
    { kind: "access", error: { statusCode: 403 } },
    { kind: "server", error: { response: { status: 503 } } },
  ])(
    "answers a $kind failure with status and a working retry",
    async ({ error }) => {
      detail().error = error;
      render(view());
      expect(screen.getByTestId("run-tray").textContent).toBe(
        "1 run · Status unavailable",
      );
      expect(screen.getByTestId("run-tray-dock").textContent).toBe(
        "1 run · Status unavailable",
      );
      openTray();
      expect(screen.getByRole("status").textContent).toBe("Status unavailable");
      expect(screen.queryByText("Checking…")).toBeNull();
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Retry" }));
      });
      expect(detail().refetch).toHaveBeenCalledTimes(1);
      expect(useRunTray.getState().runs).toEqual([ENTRY]);
    },
  );

  it("does not present cached status as current after failure and recovers on retry", async () => {
    detail().data = { runs: [RUN] };
    const rendered = render(view());
    expect(screen.getByTestId("run-tray").textContent).toBe("1 run going");
    detail().error = { statusCode: 500 };
    rendered.rerender(view());
    expect(screen.getByTestId("run-tray").textContent).toBe(
      "1 run · Status unavailable",
    );
    openTray();
    expect(screen.getByRole("status").textContent).toBe("Status unavailable");
    expect(screen.queryByText("Running")).toBeNull();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    });
    detail().isFetching = true;
    rendered.rerender(view());
    expect(screen.getByRole("button", { name: "Retry" }).disabled).toBe(false);
    detail().error = null;
    detail().isFetching = false;
    rendered.rerender(view());
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(screen.getByTestId("run-tray").textContent).toBe("1 run going");
    expect(screen.getByTestId("run-tray-dock").textContent).toBe(
      "1 run going · #4A",
    );
    expect(useRunTray.getState().runs).toEqual([ENTRY]);
  });

  it("does not toast cached completion from a failed read", () => {
    detail().data = { runs: [{ ...RUN, status: "Completed" }] };
    detail().error = { statusCode: 500 };
    render(view());
    openTray();
    expect(screen.getByRole("status").textContent).toBe("Status unavailable");
    expect(notify).not.toHaveBeenCalled();
    expect(useRunTray.getState().runs).toEqual([ENTRY]);
  });

  it("retries only the failing experiment and keeps other tracked runs", async () => {
    const other = {
      ...ENTRY,
      experimentId: "experiment-other",
      runId: "run-other",
      label: "#5A",
    };
    details.set(other.experimentId, {
      data: {
        runs: [
          {
            ...RUN,
            run_id: other.runId,
            experiment_id: other.experimentId,
            label: other.label,
          },
        ],
      },
      error: null,
      isFetching: false,
      refetch: vi.fn(),
    });
    detail().error = { statusCode: 500 };
    useRunTray.setState({ runs: [ENTRY, other] });
    render(view());
    openTray();
    expect(screen.getAllByRole("button", { name: "Retry" })).toHaveLength(1);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    });
    expect(detail().refetch).toHaveBeenCalledTimes(1);
    expect(details.get(other.experimentId).refetch).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole("button", { name: "Dismiss #4A from the tray" }),
    );
    expect(useRunTray.getState().runs).toEqual([other]);
    expect(screen.getByTestId("run-tray").textContent).toBe("1 run going");
  });

  it("preserves the neutral missing-experiment removal", () => {
    detail().error = { statusCode: 404 };
    render(view());
    expect(useRunTray.getState().runs).toHaveLength(0);
    expect(screen.queryByTestId("run-tray")).toBeNull();
    expect(notify).toHaveBeenCalledWith("Run #4A was removed", {
      key: `run-tray-removed-${ENTRY.experimentId}`,
      variant: "default",
    });
  });

  it("does not count a missing status as known progress in a mixed tray", () => {
    const other = { ...ENTRY, runId: "run-other" };
    expect(trayLabel([ENTRY, other], { [RUN.run_id]: RUN }, () => false)).toBe(
      "2 runs · Checking…",
    );
  });
});
