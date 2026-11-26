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
const EXISTING_ID = "11111111-1111-4111-8111-111111111111";
const NEW_ID = "22222222-2222-4222-8222-222222222222";
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
  id: EXISTING_ID,
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
  http.post.mockResolvedValue(ok({ id: NEW_ID }));
};

const start = async ({ billed = true, scopeFilters = [], navigate = vi.fn() } = {}) => {
  render(
    <KeepAnswering
      projectId="p1"
      run={{ items: ITEMS, scopeFilters }}
      navigate={navigate}
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
  it.each(["running", "pending", "paused"])(
    "finds the matching %s task without claiming live execution or creating another",
    async (status) => {
      const navigate = vi.fn();
      serve({ tasks: [{ ...LIVE_ROW, status }] });
      await start({ navigate });
      expect(screen.getByRole("alert")).toHaveTextContent(
        "A matching task is already saved. Check its status on the task page.",
      );
      expect(screen.getByRole("alert")).not.toHaveTextContent(/Live\.|already being answered/);
      fireEvent.click(screen.getByRole("button", { name: "View task" }));
      expect(navigate).toHaveBeenCalledWith(`/dashboard/tasks/${EXISTING_ID}`);
      expect(http.post).not.toHaveBeenCalled();
    },
  );

  it("acknowledges a newly saved task without claiming live execution", async () => {
    serve({
      tasks: [
        { ...LIVE_ROW, id: "other", evals_applied: ["Why did they call?"] },
      ],
    });
    const navigate = vi.fn();
    const confirm = await start({ navigate });
    expect(confirm).toMatch(/billed as one eval run/);
    expect(http.post).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Task saved. Check its status on the task page.",
    );
    expect(screen.getByRole("alert")).not.toHaveTextContent(/Live\.|already being answered/);
    fireEvent.click(screen.getByRole("button", { name: "View task" }));
    expect(navigate).toHaveBeenCalledWith(`/dashboard/tasks/${NEW_ID}`);
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
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Task saved. Check its status on the task page.",
    );
  });

  it.each([
    {},
    ok({}),
    ok({ id: "" }),
    ok({ id: "   " }),
    ok({ id: 123 }),
    ok({ id: {} }),
    ok({ id: "not-a-task-id" }),
    { data: { status: false, result: { id: NEW_ID } } },
    { data: { result: { id: NEW_ID } } },
  ])("does not claim saving or live execution for an unreadable create reply %#", async (reply) => {
    serve();
    http.post.mockResolvedValue(reply);
    await start();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Creation unconfirmed. Check Tasks before creating another.",
    );
    expect(screen.getByRole("alert")).not.toHaveTextContent(/Live\.|try again|retry|nothing was started/i);
    expect(screen.queryByRole("button", { name: "View task" })).not.toBeInTheDocument();
    expect(http.post).toHaveBeenCalledTimes(1);
  });

  it.each([
    new Error("Network reply lost"),
    new Error("Unreadable API contract response"),
    { statusCode: 503, result: "private provider payload" },
  ])("treats a lost or rejected unreadable response as creation unconfirmed %#", async (error) => {
    serve();
    http.post.mockRejectedValue(error);
    await start();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Creation unconfirmed. Check Tasks before creating another.",
    );
    expect(screen.getByRole("alert")).not.toHaveTextContent(/Live\.|try again|retry|nothing was started|private provider/i);
    expect(screen.queryByRole("button", { name: "View task" })).not.toBeInTheDocument();
    expect(http.post).toHaveBeenCalledTimes(1);
  });

  it("keeps safe explicit validation rejection feedback", async () => {
    serve();
    http.post.mockRejectedValue({
      statusCode: 400,
      result: "Eval configs not found for project",
    });
    await start();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Eval configs not found for project",
    );
    expect(screen.queryByRole("button", { name: "View task" })).not.toBeInTheDocument();
    expect(http.post).toHaveBeenCalledTimes(1);
  });

  it("distinguishes lookup failure before any create attempt", async () => {
    serve();
    http.get.mockRejectedValue(new Error("Lookup unavailable"));
    await start();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Could not check for an existing task. No new task was created.",
    );
    expect(http.post).not.toHaveBeenCalled();
  });

  it("preserves the permission gate", () => {
    serve();
    render(
      <KeepAnswering
        projectId="p1"
        run={{ items: ITEMS, scopeFilters: [] }}
        navigate={vi.fn()}
        disabled
      />,
    );
    expect(screen.getByRole("button", { name: "Keep answering new traces" })).toBeDisabled();
    expect(http.get).not.toHaveBeenCalled();
    expect(http.post).not.toHaveBeenCalled();
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
