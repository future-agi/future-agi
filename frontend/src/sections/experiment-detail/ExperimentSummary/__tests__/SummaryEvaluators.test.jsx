import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  setLaunchOverrideForTest,
  surfaceShownIn,
} from "src/launch/launchSwitch";
import SummaryEvaluators from "../SummaryEvaluators";

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: http,
  endpoints: {
    develop: {
      experiment: {
        summaryEvaluators: (id) => `/experiments/${id}/summary-evaluators/`,
        summaryScores: (id, run) =>
          `/experiments/${id}/runs/${run}/summary-scores/`,
      },
    },
  },
}));
vi.mock("src/sections/evals/hooks/useEvalsList", () => ({
  useEvalsList: () => ({
    data: { items: [{ id: "template", name: "Aggregate", eval_type: "code" }] },
  }),
}));
vi.mock("src/sections/evals/hooks/useEvalVersions", () => ({
  useEvalVersions: () => ({
    data: { versions: [{ id: "version", version_number: 2 }] },
  }),
}));

const clients = [];
const surface = "exp.summary-evaluators";
const state = (claimed) => ({
  launch: true,
  experimentsBasic: true,
  madeReal: new Set(claimed ? [surface] : []),
});
const response = (result) => ({ data: { result } });

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  render(
    <QueryClientProvider client={client}>
      <SummaryEvaluators experimentId="experiment" runIds={["run"]} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  http.get.mockReset();
  http.post.mockReset();
  setLaunchOverrideForTest({ ...state(true), showUnreleased: false });
});
afterEach(() => {
  cleanup();
  clients.forEach((client) => client.clear());
  clients.length = 0;
  setLaunchOverrideForTest(null);
  vi.useRealTimers();
});

describe("Summary evaluators", () => {
  it.each([false, true])(
    "shows a scope line without a claim with experiment pages=%s",
    async (experimentsBasic) => {
      setLaunchOverrideForTest({
        ...state(false),
        experimentsBasic,
        showUnreleased: true,
      });
      http.get.mockResolvedValue(response({ bindings: [], scores: [] }));
      mount();
      expect(screen.getByTestId(`${surface}-scope-line`)).toHaveTextContent(
        "Run saved prompt or code evaluators over every row of a sealed run, not just the filtered table.",
      );
      expect(
        screen.getByRole("button", { name: "Add summary evaluator" }),
      ).toBeVisible();
      expect(
        surfaceShownIn(surface, { ...state(false), experimentsBasic }),
      ).toBe(true);
      await waitFor(() => expect(http.get).toHaveBeenCalledTimes(2));
      expect(http.post).not.toHaveBeenCalled();
    },
  );

  it("binds the selected saved template version and aggregate input mapping", async () => {
    http.get.mockResolvedValue(response({ bindings: [] }));
    http.post.mockResolvedValue(response({ id: "new-binding" }));
    mount();
    fireEvent.click(
      screen.getByRole("button", { name: "Add summary evaluator" }),
    );
    fireEvent.change(screen.getByLabelText(/Summary name/), {
      target: { value: "Accuracy" },
    });
    fireEvent.mouseDown(screen.getByLabelText(/^Evaluator/));
    fireEvent.click(await screen.findByRole("option", { name: "Aggregate" }));
    fireEvent.mouseDown(screen.getByLabelText(/Pinned version/));
    fireEvent.click(await screen.findByRole("option", { name: "v2" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Save summary evaluator" }),
    );
    await waitFor(() =>
      expect(http.post).toHaveBeenCalledWith(
        "/experiments/experiment/summary-evaluators/",
        {
          name: "Accuracy",
          template_id: "template",
          version_id: "version",
          mapping: { rows: "input.rows" },
        },
      ),
    );
  });

  it("shows the stored score, reason and version without evaluating", async () => {
    http.get.mockImplementation((url) =>
      Promise.resolve(
        response(
          url.endsWith("summary-evaluators/")
            ? {
                bindings: [
                  { id: "binding", name: "Accuracy", version_number: 2 },
                ],
              }
            : {
                scores: [
                  {
                    id: "result",
                    binding_id: "binding",
                    status: "completed",
                    score: 0,
                    reason: "No correct answers",
                    version_number: 2,
                    metadata: { n: 3 },
                  },
                ],
              },
        ),
      ),
    );
    mount();
    expect(await screen.findByText("No correct answers")).toBeInTheDocument();
    expect(screen.getByText("0")).toBeInTheDocument();
    expect(screen.getByText("Accuracy · v2")).toBeInTheDocument();
    expect(http.post).toHaveBeenCalledTimes(0);
  });

  it.each(["", "dispatch_failed"])(
    "recovers unpublished work with error code %j through scheduling replay",
    async (errorCode) => {
      http.get.mockImplementation((url) =>
        Promise.resolve(
          response(
            url.endsWith("summary-evaluators/")
              ? {
                  bindings: [
                    { id: "binding", name: "Accuracy", version_number: 2 },
                  ],
                }
              : {
                  scores: [
                    {
                      id: "result",
                      binding_id: "binding",
                      status: "pending",
                      score: null,
                      publication_pending: true,
                      error_code: errorCode,
                    },
                  ],
                },
          ),
        ),
      );
      http.post.mockResolvedValue(response({ status: "pending" }));
      mount();
      expect(
        await screen.findByText("Pending publication"),
      ).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Retry scheduling" }));
      await waitFor(() =>
        expect(http.post).toHaveBeenCalledWith(
          "/experiments/experiment/runs/run/summary-scores/",
          { binding_id: "binding", retry_failed: false },
        ),
      );
      expect(http.post).toHaveBeenCalledTimes(1);
      expect(screen.queryByText("0")).not.toBeInTheDocument();
    },
  );

  it("refreshes an empty mounted view through automatic scheduling and completion", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    let scores = [];
    const readScores = vi.fn(() => response({ scores }));
    http.get.mockImplementation((url) =>
      Promise.resolve(
        url.endsWith("summary-evaluators/")
          ? response({
              bindings: [
                { id: "binding", name: "Accuracy", version_number: 2 },
              ],
            })
          : readScores(),
      ),
    );
    mount();
    expect(await screen.findByText("Not evaluated")).toBeInTheDocument();
    expect(readScores).toHaveBeenCalledTimes(1);

    // The backend seals the run and schedules work without a UI mutation.
    scores = [{ id: "result", binding_id: "binding", status: "pending" }];
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(await screen.findByText("pending")).toBeInTheDocument();
    expect(readScores).toHaveBeenCalledTimes(2);

    scores = [
      {
        id: "result",
        binding_id: "binding",
        status: "completed",
        score: 0.75,
        reason: "Aggregate complete",
        metadata: { n: 3 },
      },
    ];
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(await screen.findByText("Aggregate complete")).toBeInTheDocument();
    expect(screen.getByText("0.75")).toBeInTheDocument();
    expect(screen.queryByText("Not evaluated")).not.toBeInTheDocument();
    await act(async () => vi.advanceTimersByTimeAsync(9000));
    expect(readScores).toHaveBeenCalledTimes(3);
    expect(http.post).toHaveBeenCalledTimes(0);
  });

  it("bounds absent-result polling and permits an explicit score refresh", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    let scores = [];
    const readScores = vi.fn(() => response({ scores }));
    http.get.mockImplementation((url) =>
      Promise.resolve(
        url.endsWith("summary-evaluators/")
          ? response({
              bindings: [
                { id: "binding", name: "Accuracy", version_number: 2 },
              ],
            })
          : readScores(),
      ),
    );
    mount();
    expect(await screen.findByText("Not evaluated")).toBeInTheDocument();
    for (let index = 0; index < 100; index += 1) {
      await act(async () => vi.advanceTimersByTimeAsync(3000));
    }
    expect(readScores).toHaveBeenCalledTimes(100);
    await act(async () => vi.advanceTimersByTimeAsync(9000));
    expect(readScores).toHaveBeenCalledTimes(100);
    scores = [
      {
        id: "result",
        binding_id: "binding",
        status: "completed",
        score: 1,
        reason: "Completed later",
      },
    ];
    fireEvent.click(screen.getByRole("button", { name: "Refresh scores" }));
    expect(await screen.findByText("Completed later")).toBeInTheDocument();
    expect(readScores).toHaveBeenCalledTimes(101);
    expect(http.post).toHaveBeenCalledTimes(0);
  });

  it("requests an explicit failed retry and never displays a failed zero", async () => {
    http.get.mockImplementation((url) =>
      Promise.resolve(
        response(
          url.endsWith("summary-evaluators/")
            ? {
                bindings: [
                  { id: "binding", name: "Accuracy", version_number: 2 },
                ],
              }
            : {
                scores: [
                  {
                    id: "result",
                    binding_id: "binding",
                    status: "failed",
                    score: null,
                    error_code: "evaluation_failed",
                    version_number: 2,
                  },
                ],
              },
        ),
      ),
    );
    http.post.mockResolvedValue(response({ status: "pending" }));
    mount();
    fireEvent.click(
      await screen.findByRole("button", { name: "Retry failed" }),
    );
    await waitFor(() =>
      expect(http.post).toHaveBeenCalledWith(
        "/experiments/experiment/runs/run/summary-scores/",
        { binding_id: "binding", retry_failed: true },
      ),
    );
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });
});
