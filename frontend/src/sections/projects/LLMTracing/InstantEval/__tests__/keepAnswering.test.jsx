import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/axios", () => ({
  default: { get: http.get, post: http.post },
  endpoints: {
    project: {
      getEvalTaskConfig: () => "/configs",
      createEvalTask: () => "/task",
      getEvalTaskList: () => "/tasks",
    },
    develop: { eval: {} },
  },
}));

import KeepAnswering from "../results/KeepAnswering";

const ok = (result) => ({ data: { status: true, result } });
const QUESTION = "Did the user get help?";
const CONFIG = {
  id: "c1",
  name: QUESTION,
  eval_template: "t1",
  config: {
    instant_eval: {
      question: QUESTION,
      answer_type: "yes_no",
      labels: ["Yes", "No"],
      reads: "input_output",
      unit: "traces",
    },
  },
};
// The run page's questions: from the run's summary, without evaluator names.
const ITEMS = [
  {
    configId: "c1",
    question: QUESTION,
    fullQuestion: QUESTION,
    answerType: "yes_no",
    labels: ["Yes", "No"],
    definitions: {},
  },
];
// A row of list_eval_tasks as the backend serializes it.
const LIVE_ROW = {
  id: "live-1",
  name: `Instant eval (live) · ${QUESTION}`,
  status: "running",
  run_type: "continuous",
  row_type: "traces",
  filters_applied: { project_id: "p1" },
  created_at: "2026-10-15T10:00:00Z",
  evals_applied: [QUESTION],
  sampling_rate: 100,
  last_run: null,
};

const serve = ({ tasks = [], configs = [CONFIG] } = {}) => {
  http.get.mockImplementation((url) => {
    if (url === "/configs") return Promise.resolve(ok(configs));
    if (url === "/tasks")
      return Promise.resolve(
        ok({ metadata: { total_rows: tasks.length }, table: tasks }),
      );
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  http.post.mockResolvedValue(ok({ id: "live-new" }));
};

const start = async ({ billed = true, scopeFilters = [] } = {}) => {
  render(
    <KeepAnswering
      projectId="p1"
      run={{ items: ITEMS, scopeFilters }}
      navigate={vi.fn()}
      billed={billed}
    />,
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Keep answering new traces" }),
  );
  // What the confirm said, before it gives way to the outcome.
  const confirm = screen.getByText(/Jev will answer this question/).textContent;
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
  });
  return confirm;
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("Keep answering", () => {
  it("finds the live task already answering these questions, starting none", async () => {
    serve({ tasks: [LIVE_ROW] });
    await start();
    expect(
      screen.getByText(
        "These questions are already being answered for new traces that match this view.",
      ),
    ).toBeInTheDocument();
    expect(http.post).not.toHaveBeenCalled();
  });

  it("starts one when none answers them", async () => {
    serve({
      tasks: [
        { ...LIVE_ROW, id: "other", evals_applied: ["Why did they call?"] },
      ],
    });
    const confirm = await start();
    expect(confirm).toMatch(/billed as one eval run/);
    expect(http.post).toHaveBeenCalledTimes(1);
    expect(
      screen.getByText(/^Live\. New traces that match this view/),
    ).toBeInTheDocument();
  });

  it("starts one over this view when the live task keeps to another", async () => {
    const STATUS = {
      column_id: "status",
      filter_config: {
        filter_type: "text",
        filter_op: "equals",
        filter_value: "ERROR",
      },
    };
    serve({ tasks: [LIVE_ROW] });
    await start({ scopeFilters: [STATUS] });
    expect(http.post).toHaveBeenCalledWith(
      "/task",
      expect.objectContaining({
        evals: ["c1"],
        filters: { filters: [STATUS] },
        run_type: "continuous",
      }),
    );
    expect(
      screen.getByText(/^Live\. New traces that match this view/),
    ).toBeInTheDocument();
  });

  it("starts nothing when it can't tell, and says why", async () => {
    serve({ tasks: [LIVE_ROW], configs: [] });
    await start();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "One of these questions' evaluators is gone, so it can't be answered live.",
    );
    expect(http.post).not.toHaveBeenCalled();
  });

  it("says nothing of billing where answers aren't billed", async () => {
    serve({ tasks: [LIVE_ROW] });
    const confirm = await start({ billed: false });
    expect(confirm).not.toMatch(/billed/);
    expect(confirm).toMatch(/\. TypeSafe bills its tokens to your own key\.$/);
  });
});
