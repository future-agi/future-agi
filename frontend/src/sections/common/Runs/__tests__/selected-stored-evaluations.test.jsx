/* eslint-disable react/prop-types */
import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { endpoints } from "src/utils/axios";
import SelectionBar from "src/sections/experiment-detail/results/SelectionBar";
import { useLeaderboardActions } from "src/sections/develop-detail/ExperimentTab/useLeaderboardActions";
import { useRescore } from "../useRescore";

const h = vi.hoisted(() => ({
  post: vi.fn(),
  notify: vi.fn(),
  noop: () => {},
}));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { post: h.post },
}));
vi.mock("src/sections/common/ux/toast", async (original) => ({
  ...(await original()),
  notify: h.notify,
}));
vi.mock("src/sections/common/ux/oneview/ActionBar", () => ({
  default: ({ actions }) => {
    const score = actions.find((action) => action.id === "scoreWith");
    return (
      score && (
        <button disabled={score.disabled} onClick={score.menu[0].run}>
          Score selection
        </button>
      )
    );
  },
}));
vi.mock("src/sections/common/Access/useDatasetAccess", () => ({
  default: () => ({ can: { edit: true, run: true }, reason: h.noop }),
}));
vi.mock("src/sections/common/Selection/useSelectAllMatching", () => ({
  useSelectAllMatching: ({ selectedIds }) => ({ ids: selectedIds }),
}));
vi.mock("src/sections/common/Selection/SelectionCount", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/Selection/askFalconAboutRows", () => ({
  askFalconAboutRows: h.noop,
}));
vi.mock("src/sections/common/export/ExportDialog", () => ({
  ResultsExportDialog: () => null,
}));
vi.mock("src/sections/develop-detail/Review/StartReviewDialog", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/Collab/AssignDialog", () => ({
  default: () => null,
}));
vi.mock(
  "src/sections/experiment-detail/results/AddResultsToDatasetDialog",
  () => ({ default: () => null }),
);

const RUN = {
  run_id: "11111111-1111-4111-8111-111111111111",
  experiment_id: "22222222-2222-4222-8222-222222222222",
  output_column_id: "33333333-3333-4333-8333-333333333333",
  label: "Selected",
  task_kind: "prompt",
  metric_keys: ["tone"],
};
const EXAMPLE = "44444444-4444-4444-8444-444444444444";
const METRIC = "55555555-5555-4555-8555-555555555555";
const KEY = "66666666-6666-4666-8666-666666666666";
const STARTED = {
  data: {
    result: { message: "Cell rerun has started.", workflow_id: "scoring" },
  },
};
const bodies = {
  results: {
    user_eval_metric_ids: [METRIC],
    cells: [
      {
        column_id: RUN.output_column_id,
        run_id: RUN.run_id,
        example_id: EXAMPLE,
      },
    ],
  },
  leaderboard: { user_eval_metric_ids: [METRIC], source_ids: [RUN.run_id] },
};
function Leaderboard({ runs = [RUN] }) {
  const actions = useLeaderboardActions("dataset", {
    runs,
    metricDefs: [{ key: "tone", name: "Tone", user_eval_id: METRIC }],
  });
  return (
    <>
      <button onClick={() => actions.requestRerun({ mode: "rescore", runs })}>
        Re-score selection
      </button>
      {actions.dialogs}
    </>
  );
}
function CustomPost() {
  const scoring = useRescore({ datasetId: "dataset" });
  return (
    <>
      <button
        onClick={() =>
          scoring.request({
            posts: [
              { url: "/custom-evaluation", body: { eval_ids: [METRIC] } },
            ],
            evalNames: ["Tone"],
            estimate: false,
          })
        }
      >
        Custom scoring
      </button>
      {scoring.dialog}
    </>
  );
}
const clients = [];
function open(door, readOnly = false, runs = [RUN]) {
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
          {door === "results" ? (
            <SelectionBar
              datasetId="dataset"
              runs={[RUN]}
              focusedRunId={RUN.run_id}
              selectedRows={[{ example_id: EXAMPLE }]}
              onClear={h.noop}
              readOnly={readOnly}
              summary={{
                metrics: [
                  {
                    key: "tone",
                    name: "Tone",
                    uem_ids: { [RUN.run_id]: METRIC },
                  },
                ],
              }}
            />
          ) : door === "leaderboard" ? (
            <Leaderboard runs={runs} />
          ) : (
            <CustomPost />
          )}
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );
  const name =
    door === "results"
      ? "Score selection"
      : door === "leaderboard"
        ? "Re-score selection"
        : "Custom scoring";
  fireEvent.click(screen.getByRole("button", { name }));
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("crypto", { randomUUID: vi.fn(() => KEY) });
  h.post.mockResolvedValue(STARTED);
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.unstubAllGlobals();
});

it.each(["results", "leaderboard"])(
  "%s sends the exact stored selection only after confirmation",
  async (door) => {
    open(door);
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByText(
        "Judge calls can't be counted before this starts.",
      ),
    ).toBeInTheDocument();
    expect(h.post.mock.calls).toEqual([]);
    fireEvent.click(
      within(dialog).getByRole("button", {
        name: door === "results" ? "Score" : "Re-score",
      }),
    );
    await waitFor(() =>
      expect(h.post.mock.calls).toEqual([
        [
          endpoints.develop.experiment.reRunExperimentCell(RUN.experiment_id),
          bodies[door],
          { headers: { "Idempotency-Key": KEY } },
        ],
      ]),
    );
  },
);

it.each(["results", "leaderboard"])(
  "%s retries an unconfirmed submission with identical scope and identity",
  async (door) => {
    h.post.mockRejectedValueOnce(new Error("Connection interrupted"));
    open(door);
    const label = door === "results" ? "Score" : "Re-score";
    fireEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: label,
      }),
    );
    await waitFor(() =>
      expect(h.notify).toHaveBeenCalledWith("Connection interrupted", {
        variant: "error",
      }),
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: label })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: label }));
    await waitFor(() => expect(h.post).toHaveBeenCalledTimes(2));
    expect(h.post.mock.calls).toEqual(
      [0, 1].map(() => [
        endpoints.develop.experiment.reRunExperimentCell(RUN.experiment_id),
        bodies[door],
        { headers: { "Idempotency-Key": KEY } },
      ]),
    );
    expect(crypto.randomUUID).toHaveBeenCalledTimes(1);
  },
);

it.each(
  ["results", "leaderboard"].flatMap((door) =>
    [
      "this evaluator needs the run's original prompt settings, which weren't recorded; re-run the experiment to score it with its current configuration",
      "Selected HTTP stored-output scoring is not supported by this endpoint. Use Run evaluations for stored HTTP outputs instead.",
    ].map((message) => ({ door, message })),
  ),
)(
  "$door surfaces the stored-output refusal: $message",
  async ({ door, message }) => {
    h.post.mockRejectedValueOnce({
      message: "Request failed with status code 400",
      response: { status: 400, data: { status: false, result: message } },
    });
    open(door);
    const invalidate = vi.spyOn(
      clients[clients.length - 1],
      "invalidateQueries",
    );
    const dialog = await screen.findByRole("dialog");
    const label = door === "results" ? "Score" : "Re-score";
    expect(h.post.mock.calls).toEqual([]);
    fireEvent.click(within(dialog).getByRole("button", { name: label }));
    await waitFor(() =>
      expect(h.notify.mock.calls).toEqual([[message, { variant: "error" }]]),
    );
    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: label })).toBeEnabled(),
    );
    expect(invalidate.mock.calls).toEqual([]);
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(h.post.mock.calls).toEqual([
      [
        endpoints.develop.experiment.reRunExperimentCell(RUN.experiment_id),
        bodies[door],
        { headers: { "Idempotency-Key": KEY } },
      ],
    ]);
  },
);

it("cancelling a selected score sends no estimate or submission", async () => {
  open("results");
  fireEvent.click(
    within(await screen.findByRole("dialog")).getByRole("button", {
      name: "Cancel",
    }),
  );
  expect(h.post.mock.calls).toEqual([]);
});

it("preserves the Results read-only gate", () => {
  open("results", true);
  expect(
    screen.getByRole("button", { name: "Score selection" }),
  ).toBeDisabled();
  expect(h.post.mock.calls).toEqual([]);
});

it("keeps a custom evaluation post unchanged", async () => {
  open("custom");
  expect(h.post.mock.calls).toEqual([]);
  fireEvent.click(
    within(await screen.findByRole("dialog")).getByRole("button", {
      name: "Re-score",
    }),
  );
  await waitFor(() =>
    expect(h.post.mock.calls).toEqual([
      ["/custom-evaluation", { eval_ids: [METRIC] }],
    ]),
  );
});

it("leaderboard preserves both selected executions of one variant on retry", async () => {
  const older = { ...RUN, run_id: "77777777-7777-4777-8777-777777777777" };
  h.post.mockRejectedValueOnce(new Error("Connection interrupted"));
  open("leaderboard", false, [older, RUN]);
  expect(h.post.mock.calls).toEqual([]);
  fireEvent.click(
    within(await screen.findByRole("dialog")).getByRole("button", {
      name: "Re-score",
    }),
  );
  await waitFor(() =>
    expect(h.notify).toHaveBeenCalledWith("Connection interrupted", {
      variant: "error",
    }),
  );
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Re-score" })).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole("button", { name: "Re-score" }));
  await waitFor(() => expect(h.post).toHaveBeenCalledTimes(2));
  expect(h.post.mock.calls).toEqual(
    [0, 1].map(() => [
      endpoints.develop.experiment.reRunExperimentCell(RUN.experiment_id),
      {
        user_eval_metric_ids: [METRIC],
        source_ids: [older.run_id, RUN.run_id],
      },
      { headers: { "Idempotency-Key": KEY } },
    ]),
  );
  expect(crypto.randomUUID).toHaveBeenCalledTimes(1);
});
