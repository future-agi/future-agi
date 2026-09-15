/* eslint-disable react/prop-types */
import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const { feedbackListHook } = vi.hoisted(() => ({
  feedbackListHook: vi.fn(),
}));

vi.mock("../../hooks/useEvalFeedback", () => ({
  useEvalFeedbackList: (...args) => feedbackListHook(...args),
}));

vi.mock("@monaco-editor/react", () => ({
  default: ({ value }) => <pre data-testid="json-editor">{value}</pre>,
}));

const { drawerProps } = vi.hoisted(() => ({
  drawerProps: { current: null },
}));

vi.mock(
  "src/sections/evals/EvalDetails/EvalsFeedback/AddEvalsFeedbackDrawer",
  () => ({
    default: (props) => {
      drawerProps.current = props;
      return props.open ? <div data-testid="edit-feedback-drawer" /> : null;
    },
  }),
);

vi.mock("src/components/data-table", () => ({
  DataTable: ({ columns, data, onRowClick, emptyMessage, isLoading }) => {
    if (isLoading) return <div data-testid="feedback-loading">Loading...</div>;
    if (!data.length) return <div>{emptyMessage}</div>;
    return (
      <div data-testid="feedback-table">
        {data.map((row) => (
          <div
            key={row.id}
            data-testid={`feedback-row-${row.id}`}
            onClick={() => onRowClick(row)}
          >
            {columns.map((col) => {
              const value = row[col.accessorKey || col.id];
              return (
                <span key={col.id}>
                  {col.cell ? col.cell({ getValue: () => value }) : value}
                </span>
              );
            })}
          </div>
        ))}
      </div>
    );
  },
  DataTablePagination: () => <div data-testid="feedback-pagination" />,
}));

import EvalFeedbackTab from "../EvalFeedbackTab";

function renderTab(props = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  function Wrapper({ children }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  }
  Wrapper.propTypes = { children: PropTypes.node };
  return render(
    <Wrapper>
      <EvalFeedbackTab templateId="tmpl-1" {...props} />
    </Wrapper>,
  );
}

const passedRow = {
  id: "fb-1",
  value: "passed",
  explanation: "Matches the reference answer",
  action_type: "retune",
  source: "eval_playground",
  user_name: "Ada Lovelace",
  created_at: "2026-08-01T12:00:00Z",
  source_id: "log-1",
};

const failedRow = {
  id: "fb-2",
  value: "failed",
  explanation: "Missed the edge case",
  action_type: null,
  source: "dataset",
  user_name: "Grace Hopper",
  created_at: "2026-08-02T09:30:00Z",
};

describe("EvalFeedbackTab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    drawerProps.current = null;
  });

  it("shows a loading state before data resolves", () => {
    feedbackListHook.mockReturnValue({
      data: undefined,
      isLoading: true,
      isFetching: true,
    });

    renderTab();

    expect(screen.getByTestId("feedback-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("feedback-table")).not.toBeInTheDocument();
  });

  it("renders feedback rows from the list hook", () => {
    feedbackListHook.mockReturnValue({
      data: { items: [passedRow, failedRow], total: 2 },
      isLoading: false,
      isFetching: false,
    });

    renderTab();

    const passed = screen.getByTestId("feedback-row-fb-1");
    expect(passed).toHaveTextContent("Correct");
    expect(passed).toHaveTextContent("Matches the reference answer");
    expect(passed).toHaveTextContent("Re-tune");
    expect(passed).toHaveTextContent("Playground");
    expect(passed).toHaveTextContent("Ada Lovelace");

    const failed = screen.getByTestId("feedback-row-fb-2");
    expect(failed).toHaveTextContent("Incorrect");
    expect(failed).toHaveTextContent("Missed the edge case");
    expect(failed).toHaveTextContent("Dataset");
    expect(failed).toHaveTextContent("Grace Hopper");
  });

  it("shows the empty state when there is no feedback yet", () => {
    feedbackListHook.mockReturnValue({
      data: { items: [], total: 0 },
      isLoading: false,
      isFetching: false,
    });

    renderTab();

    expect(screen.getByText("No feedback submitted yet")).toBeInTheDocument();
  });

  it("filters visible rows by the search box (value/explanation/user_name)", async () => {
    feedbackListHook.mockReturnValue({
      data: { items: [passedRow, failedRow], total: 2 },
      isLoading: false,
      isFetching: false,
    });

    renderTab();

    expect(screen.getByTestId("feedback-row-fb-1")).toBeInTheDocument();
    expect(screen.getByTestId("feedback-row-fb-2")).toBeInTheDocument();

    await userEvent.type(screen.getByPlaceholderText("Search..."), "edge case");

    await waitFor(() =>
      expect(screen.queryByTestId("feedback-row-fb-1")).not.toBeInTheDocument(),
    );
    expect(screen.getByTestId("feedback-row-fb-2")).toBeInTheDocument();
  });

  it("opens the detail side panel with the row's fields on row click", async () => {
    feedbackListHook.mockReturnValue({
      data: { items: [passedRow, failedRow], total: 2 },
      isLoading: false,
      isFetching: false,
    });

    renderTab();

    await userEvent.click(screen.getByTestId("feedback-row-fb-1"));

    // "Log ID" is a detail-panel-only field, so its value unambiguously
    // confirms the panel opened with this specific row.
    expect(await screen.findByText("log-1")).toBeInTheDocument();
    expect(screen.getByText("1 / 2")).toBeInTheDocument();
  });

  it("opens the edit-feedback drawer with the selected row when Edit Feedback is clicked", async () => {
    feedbackListHook.mockReturnValue({
      data: { items: [passedRow], total: 1 },
      isLoading: false,
      isFetching: false,
    });

    renderTab();

    await userEvent.click(screen.getByTestId("feedback-row-fb-1"));
    await userEvent.click(await screen.findByText("Edit Feedback"));

    expect(await screen.findByTestId("edit-feedback-drawer")).toBeInTheDocument();
    expect(drawerProps.current.evalsId).toBe("tmpl-1");
    expect(drawerProps.current.existingFeedback).toEqual(passedRow);
  });
});
