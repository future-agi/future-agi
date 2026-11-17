import React from "react";
import PropTypes from "prop-types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import axios, { endpoints } from "src/utils/axios";
import { fetchExperimentDetail, readEnvelope } from "src/api/develop/runs";
import {
  noteRunControl,
  resetRunControlForTest,
} from "src/sections/common/ExperimentRunControl/runControlAvailability";
import RunRowMenuHost from "../RunRowMenuHost";
import { useLeaderboardActions } from "../useLeaderboardActions";

const state = vi.hoisted(() => ({ notify: vi.fn() }));
vi.mock("../cellContext", () => ({ useCell: (props) => props }));
vi.mock("src/sections/common/Runs/useRunActions", () => ({
  default: () => ({ runControlOff: false, held: {}, simulateUpload: vi.fn() }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/common/ux/toast", async (original) => ({
  ...(await original()),
  notify: state.notify,
}));

const EXP = "experiment-one";
const DETAIL = endpoints.develop.experiment.getExperimentDetails(EXP);
const POST = endpoints.develop.experiment.reRunExperimentCell(EXP);
const SEALED = "2026-11-15T06:00:00Z";
const run = (id, status, done, completedAt) => ({
  run_id: id,
  experiment_id: EXP,
  native_config_id: "prompt-config-one",
  label: id === "original" ? "#1A" : "#1A·2",
  name: "Prompt",
  status,
  completed_at: completedAt,
  config: { kind: "prompt" },
  progress: { status: status[0].toUpperCase() + status.slice(1), total: 3, done, errors: 0 },
});
const ORIGINAL = run("original", "completed", 3, SEALED);
const PAUSED = run("paused", "cancelled", 1, null);
// The list catalog keeps the stored running status; detail projects STOPPED.
const CATALOG = [ORIGINAL, run("paused", "running", 1, null)];
let detail;
let client;
const requestRerun = vi.fn();
const menu = {
  group: false,
  experimentId: EXP,
  runIds: [ORIGINAL.run_id],
  position: { top: 20, left: 20 },
};
const context = {
  datasetId: "ds-one",
  runsPerExperiment: { [EXP]: 2 },
  allRuns: CATALOG,
  canEdit: true,
  canRun: true,
  canDelete: true,
  requestRerun,
  navigate: vi.fn(),
};

function terminalDetail() {
  return {
    id: EXP,
    dataset_id: "ds-one",
    status: "Completed",
    runs: [ORIGINAL, run("paused", "completed", 3, SEALED)],
    runs_total: 2,
    runs_has_more: false,
  };
}

function pausedDetail() {
  return { ...terminalDetail(), status: "Cancelled", runs: [ORIGINAL, PAUSED] };
}

function mount(content) {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <ThemeProvider theme={createTheme({ palette: palette("light"), customShadows: { drawer: "none" } })}>
      <QueryClientProvider client={client}>
        <MemoryRouter>{content}</MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

function openMenu(overrides = {}) {
  return mount(
    <RunRowMenuHost
      menu={menu}
      rows={CATALOG}
      context={{ ...context, ...overrides }}
      onClose={vi.fn()}
    />,
  );
}

function DispatchHarness({ mode = "all" }) {
  const actions = useLeaderboardActions("ds-one", { runs: CATALOG });
  return (
    <>
      <button
        type="button"
        onClick={() => actions.requestRerun({ mode, runs: [ORIGINAL] })}
      >
        Open rerun
      </button>
      {actions.dialogs}
    </>
  );
}

DispatchHarness.propTypes = { mode: PropTypes.oneOf(["all", "failed"]) };

async function openConfirmation(mode = "all") {
  mount(<DispatchHarness mode={mode} />);
  fireEvent.click(screen.getByRole("button", { name: "Open rerun" }));
  return screen.findByRole("button", {
    name: mode === "failed" ? "Re-run failed rows" : "Re-run",
  });
}

beforeEach(() => {
  resetRunControlForTest();
  noteRunControl("on");
  detail = pausedDetail();
  requestRerun.mockClear();
  state.notify.mockClear();
  vi.spyOn(axios, "get").mockImplementation(async (url) => {
    if (url !== DETAIL) throw new Error(`Unexpected detail read: ${url}`);
    return { data: { status: true, result: detail } };
  });
  vi.spyOn(axios, "post").mockResolvedValue({
    status: 200,
    data: { status: true, result: { message: "Cell rerun has started." } },
  });
});

afterEach(() => {
  cleanup();
  client?.clear();
  resetRunControlForTest();
  axios.get.mockRestore();
  axios.post.mockRestore();
});

describe("list Rerun uses current experiment state", () => {
  it("blocks an older completed row when only fresh detail reveals a paused sibling", async () => {
    openMenu();
    const rerun = screen.getByRole("menuitem", { name: "Rerun", exact: true });
    await waitFor(() => expect(rerun).toHaveAccessibleDescription(/Resume/));
    expect(axios.get).toHaveBeenCalledWith(DETAIL, expect.anything());
    expect(rerun).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(rerun);
    expect(screen.queryByRole("menuitem", { name: "Rerun all rows" })).toBeNull();
    expect(requestRerun).not.toHaveBeenCalled();
    expect(axios.post).not.toHaveBeenCalled();
  });

  it("keeps the completed control and exact selected-run identity", async () => {
    detail = terminalDetail();
    openMenu();
    const rerun = screen.getByRole("menuitem", { name: "Rerun", exact: true });
    await waitFor(() => expect(rerun).not.toHaveAttribute("aria-disabled", "true"));
    fireEvent.click(rerun);
    fireEvent.click(await screen.findByRole("menuitem", { name: "Rerun all rows" }));
    expect(requestRerun).toHaveBeenCalledWith({ mode: "all", runs: [ORIGINAL] });
    expect(axios.post).not.toHaveBeenCalled();
  });

  it("updates an open submenu when a detail refresh reports the pause", async () => {
    detail = terminalDetail();
    openMenu();
    const rerun = screen.getByRole("menuitem", { name: "Rerun", exact: true });
    await waitFor(() => expect(rerun).not.toHaveAttribute("aria-disabled", "true"));
    fireEvent.click(rerun);
    const all = await screen.findByRole("menuitem", { name: "Rerun all rows" });
    detail = pausedDetail();
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["experiment", EXP] });
    });
    await waitFor(() => {
      const current = screen.queryByRole("menuitem", { name: "Rerun all rows" });
      if (current) expect(current).toHaveAttribute("aria-disabled", "true");
    });
    fireEvent.click(screen.queryByRole("menuitem", { name: "Rerun all rows" }) ?? all);
    expect(requestRerun).not.toHaveBeenCalled();
    expect(axios.post).not.toHaveBeenCalled();
  });

  it("keeps the run-permission gate", () => {
    openMenu({ canRun: false });
    const rerun = screen.getByRole("menuitem", { name: "Rerun", exact: true });
    expect(rerun).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(rerun);
    expect(requestRerun).not.toHaveBeenCalled();
    expect(axios.post).not.toHaveBeenCalled();
  });
});

describe("list confirmations recheck before writing", () => {
  it.each(["all", "failed"])(
    "refreshes fresh completed detail before opening a rerun of %s rows",
    async (mode) => {
      detail = terminalDetail();
      const confirm = await openConfirmation(mode);
      const queryKey = ["experiment", EXP];
      // Opening the row menu cached Completed. Keep it fresh so this read
      // depends on mutation invalidation, not time passing or polling.
      client.setQueryDefaults(queryKey, { staleTime: Infinity });
      client.setQueryData(queryKey, {
        data: { status: true, result: detail },
      });
      const unrelatedKey = ["experiment", "unrelated"];
      client.setQueryData(unrelatedKey, {
        data: {
          status: true,
          result: { id: "unrelated", status: "Completed" },
        },
      });
      const running = {
        ...detail,
        status: "Running",
        runs: [...detail.runs, run("rerun", "running", 0, null)],
        runs_total: 3,
      };
      axios.post.mockImplementationOnce(async () => {
        detail = running;
        return {
          status: 200,
          data: {
            status: true,
            result: { message: "Cell rerun has started." },
          },
        };
      });
      fireEvent.click(confirm);
      await waitFor(() => expect(state.notify).toHaveBeenCalledTimes(1));
      expect(axios.post.mock.calls).toEqual([
        [
          POST,
          {
            source_ids: [ORIGINAL.run_id],
            ...(mode === "failed" && { failed_only: true }),
          },
        ],
      ]);
      // The next detail consumer must fetch the new active run even while
      // the menu's old detail would otherwise still be fresh.
      const reopened = await client.fetchQuery({
        queryKey,
        queryFn: () => fetchExperimentDetail(EXP),
      });
      expect(readEnvelope(reopened)).toEqual(running);
      expect(client.getQueryState(unrelatedKey).isInvalidated).toBe(false);
    },
  );

  it("refuses a stale confirmation after a different run pauses", async () => {
    detail = terminalDetail();
    const confirm = await openConfirmation();
    detail = pausedDetail();
    fireEvent.click(confirm);
    await waitFor(() =>
      expect(state.notify).toHaveBeenCalledWith(expect.stringMatching(/Resume/), expect.anything()),
    );
    expect(axios.get).toHaveBeenCalledWith(DETAIL, expect.anything());
    expect(axios.post).not.toHaveBeenCalled();
  });

  it("sends the existing source_ids body when fresh detail is terminal", async () => {
    detail = terminalDetail();
    fireEvent.click(await openConfirmation());
    await waitFor(() => expect(axios.post).toHaveBeenCalledWith(POST, { source_ids: [ORIGINAL.run_id] }));
    expect(axios.get).toHaveBeenCalledWith(DETAIL, expect.anything());
    expect(axios.post).toHaveBeenCalledTimes(1);
  });

  it("does not write or call an unreadable state a pause", async () => {
    const confirm = await openConfirmation();
    axios.get.mockRejectedValue(new Error("Experiment state unavailable"));
    fireEvent.click(confirm);
    await waitFor(() => expect(state.notify).toHaveBeenCalled());
    expect(state.notify.mock.calls.some(([message]) => String(message).includes("Resume"))).toBe(false);
    expect(axios.post).not.toHaveBeenCalled();
  });
});
