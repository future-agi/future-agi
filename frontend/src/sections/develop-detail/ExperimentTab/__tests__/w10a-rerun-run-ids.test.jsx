/* eslint-disable react/prop-types */
// Rerun all rows and Rerun failed rows on the Experiments tab
// send each run's catalog `run_id` as rerun-cells `source_ids`, one request
// per experiment, with `failed_only` only for failed rows. The server maps a
// run id to the run's variant (views/experiments.py _run_ids_as_variant_ids).
// A refusal shows the server's plain line and keeps the confirm open.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { endpoints } from "src/utils/axios";
import { useLeaderboardActions } from "../useLeaderboardActions";

const state = vi.hoisted(() => ({ post: vi.fn(), notify: vi.fn() }));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { post: state.post },
}));
vi.mock("src/sections/common/ux/toast", async (original) => ({
  ...(await original()),
  notify: state.notify,
}));

const STARTED = {
  data: {
    status: true,
    result: { message: "Cell rerun has started.", workflow_id: "wf-1" },
  },
};
const RUNS = [
  { run_id: "run-2a", label: "#2A", experiment_id: "exp-2" },
  { run_id: "run-2b", label: "#2B", experiment_id: "exp-2" },
  { run_id: "run-3a", label: "#3A", experiment_id: "exp-3" },
];
const REFUSAL =
  "Nothing was rerun. One selected run is no longer part of this experiment. Refresh the page and try again.";

function Harness({ request }) {
  const actions = useLeaderboardActions("ds-1", { runs: RUNS });
  return (
    <>
      <button type="button" onClick={() => actions.requestRerun(request)}>
        Open rerun
      </button>
      {actions.dialogs}
    </>
  );
}

const clients = [];
function openRerun(request) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  render(
    <ThemeProvider
      theme={createTheme({
        palette: palette("light"),
        customShadows: { drawer: "none" },
      })}
    >
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <Harness request={request} />
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Open rerun" }));
}

afterEach(() => {
  clients.splice(0).forEach((client) => client.clear());
  state.post.mockReset();
  state.notify.mockReset();
});

describe("Rerun rows send the runs' catalog ids", () => {
  it("Rerun all rows posts each experiment's run ids as source_ids", async () => {
    state.post.mockResolvedValue(STARTED);
    openRerun({ mode: "all", runs: RUNS });

    fireEvent.click(await screen.findByRole("button", { name: "Re-run" }));

    await waitFor(() => expect(state.post).toHaveBeenCalledTimes(2));
    expect(state.post).toHaveBeenCalledWith(
      endpoints.develop.experiment.reRunExperimentCell("exp-2"),
      { source_ids: ["run-2a", "run-2b"] },
    );
    expect(state.post).toHaveBeenCalledWith(
      endpoints.develop.experiment.reRunExperimentCell("exp-3"),
      { source_ids: ["run-3a"] },
    );
    await waitFor(() =>
      expect(state.notify).toHaveBeenCalledWith(
        "Re-running 3 runs",
        expect.objectContaining({ variant: "success" }),
      ),
    );
  });

  it("Rerun failed rows adds failed_only and names only the chosen run", async () => {
    state.post.mockResolvedValue(STARTED);
    openRerun({ mode: "failed", runs: [RUNS[1]] });

    fireEvent.click(
      await screen.findByRole("button", { name: "Re-run failed rows" }),
    );

    await waitFor(() => expect(state.post).toHaveBeenCalledTimes(1));
    expect(state.post).toHaveBeenCalledWith(
      endpoints.develop.experiment.reRunExperimentCell("exp-2"),
      { source_ids: ["run-2b"], failed_only: true },
    );
  });

  it("a refusal shows the server's plain line and keeps the confirm", async () => {
    state.post.mockRejectedValue({
      response: {
        status: 400,
        data: { status: false, message: REFUSAL, result: REFUSAL },
      },
    });
    openRerun({ mode: "all", runs: [RUNS[0]] });

    fireEvent.click(await screen.findByRole("button", { name: "Re-run" }));

    await waitFor(() =>
      expect(state.notify).toHaveBeenCalledWith(
        REFUSAL,
        expect.objectContaining({ variant: "error" }),
      ),
    );
    expect(screen.getByRole("button", { name: "Re-run" })).toBeEnabled();
  });
});
