import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: api,
  endpoints: {
    develop: {
      experiment: {
        feedback: {
          getDetails: (id) =>
            `/experiments/${id}/feedback/get-feedback-details/`,
          create: (id) => `/experiments/${id}/feedback/`,
        },
      },
    },
  },
}));
import ExperimentFeedbackDialog from "../ExperimentFeedbackDialog";
const props = {
  experimentId: "experiment-a",
  metricId: "metric-a",
  columnId: "column-a",
  rowId: "row-a",
};
const close = vi.fn();
function mount(extra = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <ExperimentFeedbackDialog {...props} onClose={close} {...extra} />
    </QueryClientProvider>,
  );
}
function fill() {
  fireEvent.change(screen.getByLabelText("Feedback value", { exact: false }), {
    target: { value: " Pass " },
  });
  fireEvent.change(screen.getByLabelText("Explanation", { exact: false }), {
    target: { value: " Expected answer " },
  });
}
beforeEach(() => {
  api.get.mockReset().mockResolvedValue({ data: { result: { feedback: [] } } });
  api.post.mockReset();
  close.mockReset();
});
afterEach(cleanup);
it("reads the scoped history and writes exactly the selected row identity", async () => {
  api.post.mockResolvedValue({ data: { result: { id: "feedback-a" } } });
  mount();
  await waitFor(() =>
    expect(api.get).toHaveBeenCalledWith(
      "/experiments/experiment-a/feedback/get-feedback-details/",
      { params: { user_eval_metric_id: "metric-a", row_id: "row-a" } },
    ),
  );
  expect(screen.getByRole("button", { name: "Save feedback" })).toBeDisabled();
  fill();
  fireEvent.click(screen.getByRole("button", { name: "Save feedback" }));
  await screen.findByText("Feedback saved");
  expect(api.post).toHaveBeenCalledExactlyOnceWith(
    "/experiments/experiment-a/feedback/",
    {
      source: "experiment",
      source_id: "column-a",
      row_id: "row-a",
      user_eval_metric: "metric-a",
      value: "Pass",
      explanation: "Expected answer",
    },
  );
  expect(screen.getByRole("button", { name: "Save feedback" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(close).toHaveBeenCalledOnce();
});
it("keeps the draft after refusal and requires a returned feedback identity", async () => {
  api.post
    .mockRejectedValueOnce(new Error("Not permitted"))
    .mockResolvedValueOnce({ data: { result: {} } });
  mount();
  fill();
  fireEvent.click(screen.getByRole("button", { name: "Save feedback" }));
  await screen.findByText("Not permitted");
  expect(screen.getByLabelText("Explanation", { exact: false })).toHaveValue(
    " Expected answer ",
  );
  fireEvent.click(screen.getByRole("button", { name: "Save feedback" }));
  await screen.findByText("Feedback was not confirmed. Please try again.");
  expect(screen.queryByText("Feedback saved")).not.toBeInTheDocument();
  expect(close).not.toHaveBeenCalled();
});
it("does not read or write when the result identity is incomplete", () => {
  mount({ rowId: undefined });
  fill();
  expect(screen.getByRole("button", { name: "Save feedback" })).toBeDisabled();
  expect(api.get).not.toHaveBeenCalled();
  expect(api.post).not.toHaveBeenCalled();
});
it("labels aggregate history and makes read failures visible", async () => {
  api.get.mockResolvedValue({
    data: {
      result: {
        feedback: [{ id: "prior", value: "Fail", comment: "Prior note" }],
      },
    },
  });
  mount();
  await screen.findByText("Prior note");
  expect(
    screen.getByText(
      "Saved feedback for this evaluator and row, across variants",
    ),
  ).toBeVisible();
  cleanup();
  api.get.mockRejectedValue(new Error("read failed"));
  mount();
  await screen.findByText("Could not load saved feedback.");
});
it("blocks closing and duplicate saves until the mutation settles", async () => {
  let complete;
  api.post.mockImplementation(
    () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  );
  mount();
  fill();
  fireEvent.click(screen.getByRole("button", { name: "Save feedback" }));
  await waitFor(() => expect(api.post).toHaveBeenCalledOnce());
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Close" })).toBeDisabled(),
  );
  /** @type {(value: { data: { result: { id: string } } }) => void} */ (
    complete
  )({ data: { result: { id: "saved" } } });
  await screen.findByText("Feedback saved");
  fireEvent.change(screen.getByLabelText("Feedback value", { exact: false }), {
    target: { value: "Fail" },
  });
  expect(screen.queryByText("Feedback saved")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save feedback" })).toBeEnabled();
});
