import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { endpoints } from "src/utils/axios";
import { datasetRunsKey } from "src/api/develop/runs";
import { useLeaderboardActions } from "../useLeaderboardActions";

const state = vi.hoisted(() => ({
  post: vi.fn(),
  notify: vi.fn(),
  done: vi.fn(),
  navigate: vi.fn(),
}));
vi.mock("react-router-dom", async (original) => ({
  ...(await original()),
  useNavigate: () => state.navigate,
}));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { post: state.post },
}));
vi.mock("src/api/develop/runs", async (original) => ({
  ...(await original()),
  fetchExperimentDetail: async (id) => ({
    data: {
      status: true,
      result: {
        id,
        runs: RUNS.filter((run) => run.experiment_id === id),
        runs_total: 1,
        runs_has_more: false,
      },
    },
  }),
}));
vi.mock("src/sections/common/ux/toast", async (original) => ({
  ...(await original()),
  notify: state.notify,
}));

const RUNS = [
  {
    run_id: "run-one",
    label: "#1A",
    experiment_id: "experiment-one",
    status: "completed",
    completed_at: "2026-01-01T00:00:00Z",
  },
  {
    run_id: "run-two",
    label: "#2A",
    experiment_id: "experiment-two",
    status: "completed",
    completed_at: "2026-01-01T00:00:00Z",
  },
];
const started = {
  data: { status: true, result: { run_ids: ["new-one"], labels: ["#1A·2"] } },
};
const refused = {
  response: {
    status: 409,
    data: { status: false, message: "Selected run is unavailable." },
  },
};
const postUrl = (id) => endpoints.develop.experiment.reRunExperimentCell(id);
function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
function Harness() {
  const actions = useLeaderboardActions("dataset-one", {
    runs: RUNS,
    onDone: state.done,
  });
  return (
    <>
      <button
        type="button"
        onClick={() => actions.requestRerun({ mode: "all", runs: RUNS })}
      >
        Open rerun
      </button>
      {actions.dialogs}
    </>
  );
}
let client;
let invalidate;
async function open() {
  render(
    <ThemeProvider
      theme={createTheme({
        palette: palette("light"),
        customShadows: { drawer: "none" },
      })}
    >
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <Harness />
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Open rerun" }));
  fireEvent.click(await screen.findByRole("button", { name: "Re-run" }));
  await waitFor(() => expect(state.post.mock.calls.length).toBe(2));
}
const messages = () =>
  state.notify.mock.calls.map(([text, opts]) => [text, opts.variant]);

beforeEach(() => {
  vi.clearAllMocks();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  invalidate = vi.spyOn(client, "invalidateQueries");
});
afterEach(() => {
  cleanup();
  client.clear();
});

describe("bulk rerun outcomes", () => {
  it.each(["success-first", "refusal-first"])(
    "keeps successful identities and retries only refused members with %s",
    async (order) => {
      const success = deferred();
      const failure = deferred();
      state.post.mockImplementation((url) =>
        url === postUrl("experiment-one") ? success.promise : failure.promise,
      );
      await open();
      await act(async () => {
        if (order === "success-first") success.resolve(started);
        else failure.reject(refused);
      });
      await act(async () => {
        if (order === "success-first") failure.reject(refused);
        else success.resolve(started);
      });
      expect(messages()).toEqual([
        ["#2A: Selected run is unavailable.", "error"],
        ["Started #1A·2. #1A keeps its results.", "success"],
      ]);
      expect(invalidate.mock.calls).toEqual([
        [{ queryKey: datasetRunsKey("dataset-one") }],
        [{ queryKey: ["datasets", "list"] }],
        [{ queryKey: ["experiment-list"] }],
        [{ queryKey: ["experiment", "experiment-one"] }],
        [{ queryKey: ["experiment", "experiment-two"] }],
      ]);
      expect(state.done.mock.calls).toEqual([[]]);
      expect(screen.getByText("Re-run #2A?").textContent).toBe("Re-run #2A?");
      const openAction = state.notify.mock.calls[1][1].action;
      expect(typeof openAction).toBe("function");
      render(openAction());
      expect(screen.getByRole("button", { name: "Open" }).textContent).toBe(
        "Open",
      );
      fireEvent.click(screen.getByRole("button", { name: "Open" }));
      expect(state.navigate.mock.calls).toEqual([
        [
          "/dashboard/develop/experiment/experiment-one/data?datasetId=dataset-one&run=new-one",
        ],
      ]);
      state.post.mockResolvedValue({
        data: {
          status: true,
          result: { run_ids: ["new-two"], labels: ["#2A·2"] },
        },
      });
      fireEvent.click(screen.getByRole("button", { name: "Re-run" }));
      await waitFor(() => expect(state.post.mock.calls.length).toBe(3));
      expect(state.post.mock.calls).toEqual([
        [postUrl("experiment-one"), { source_ids: ["run-one"] }],
        [postUrl("experiment-two"), { source_ids: ["run-two"] }],
        [postUrl("experiment-two"), { source_ids: ["run-two"] }],
      ]);
    },
  );

  it.each([
    new Error("connection lost"),
    { statusCode: 500, message: "server error" },
  ])("does not offer retry for an unknown outcome", async (error) => {
    state.post.mockImplementation((url) =>
      url === postUrl("experiment-one")
        ? Promise.resolve(started)
        : Promise.reject(error),
    );
    await open();
    await waitFor(() => expect(messages().length).toBe(2));
    expect(messages()).toEqual([
      [
        "#2A: Rerun outcome unknown. Refresh and check for new runs before retrying.",
        "error",
      ],
      ["Started #1A·2. #1A keeps its results.", "success"],
    ]);
    expect(screen.queryByRole("button", { name: "Re-run" })).toBeNull();
    expect(state.post.mock.calls.length).toBe(2);
    expect(invalidate.mock.calls.length).toBe(5);
    expect(state.done.mock.calls).toEqual([[]]);
  });

  it("reports workflow-only success without invented identities", async () => {
    state.post.mockImplementation((url) =>
      url === postUrl("experiment-one")
        ? Promise.resolve({
            data: {
              status: true,
              result: {
                message: "Cell rerun has started.",
                workflow_id: "workflow-one",
              },
            },
          })
        : Promise.reject(refused),
    );
    await open();
    await waitFor(() => expect(messages().length).toBe(2));
    expect(messages()).toEqual([
      ["#2A: Selected run is unavailable.", "error"],
      ["Re-running #1A", "success"],
    ]);
    expect(state.notify.mock.calls[1][1].action).toBeUndefined();
    expect(invalidate.mock.calls.length).toBe(5);
    expect(screen.getByText("Re-run #2A?").textContent).toBe("Re-run #2A?");
  });

  it("refreshes after refusals and retains only those runs", async () => {
    state.post.mockRejectedValue(refused);
    await open();
    await waitFor(() => expect(messages().length).toBe(2));
    expect(messages()).toEqual([
      ["#1A: Selected run is unavailable.", "error"],
      ["#2A: Selected run is unavailable.", "error"],
    ]);
    expect(invalidate.mock.calls.length).toBe(5);
    expect(screen.getByText("Re-run 2 runs?").textContent).toBe(
      "Re-run 2 runs?",
    );
  });
});
