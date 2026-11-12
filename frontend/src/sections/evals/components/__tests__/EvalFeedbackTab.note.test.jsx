/* eslint-disable react/prop-types */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// W10-EVALS-U08 / W11-EVALS-FB-NOTE-FIELD: the Feedback tab and Edit Feedback
// show the note the person wrote. Observe keeps the eval's own explanation in
// `explanation` and the person's note in `feedback_improvement`; the drawer's
// read-only Explanation never shows the person's note as the eval's.

const mocks = vi.hoisted(() => ({ items: [], drawerProps: null }));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Admin", user: { id: "user-me" } }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("@monaco-editor/react", () => ({ default: () => null }));
vi.mock("src/hooks/use-debounce", () => ({ useDebounce: (value) => value }));
vi.mock("src/components/FormSearchField/FormSearchField", () => ({
  default: ({ value, onChange, placeholder }) => (
    <input
      aria-label="Search this page"
      placeholder={placeholder}
      value={value}
      onChange={onChange}
    />
  ),
}));
vi.mock("src/sections/develop-detail/DataTab/common", () => ({
  normalizeEvalCellValue: (value) => value,
}));
vi.mock(
  "src/sections/evals/EvalDetails/EvalsFeedback/AddEvalsFeedbackDrawer",
  () => ({
    default: (props) => {
      mocks.drawerProps = props;
      return null;
    },
  }),
);
// Renders each row's Improvement Note cell the way DataTable calls it.
vi.mock("src/components/data-table", () => ({
  DataTable: ({ columns, data, onRowClick }) => {
    const noteColumn = columns.find((column) => column.id === "explanation");
    return (
      <ul>
        {data.map((row) => (
          <li key={row.id} data-testid={`row-${row.id}`}>
            <button type="button" onClick={() => onRowClick(row)}>
              {`open ${row.id}`}
            </button>
            <span data-testid={`note-${row.id}`}>
              {noteColumn.cell({
                getValue: () => row[noteColumn.accessorKey || noteColumn.id],
                row: { original: row, getIsSelected: () => false },
                column: noteColumn,
              })}
            </span>
          </li>
        ))}
      </ul>
    );
  },
  DataTablePagination: () => null,
}));
vi.mock("../../hooks/useEvalFeedback", () => ({
  useEvalFeedbackList: () => ({
    data: { items: mocks.items, total: mocks.items.length },
    isLoading: false,
    isFetching: false,
  }),
}));

import EvalFeedbackTab from "../EvalFeedbackTab";

const EVAL_EXPLANATION =
  "The response cites a refund policy that does not exist";
const OBSERVE_NOTE = "The policy exists; it is on the pricing page";
const DATASET_NOTE = "The answer should mention the 30-day window";

const row = (overrides) => ({
  value: "failed",
  action_type: "retune",
  user_name: "Someone",
  user_id: "user-me",
  created_at: "2026-11-01T10:00:00Z",
  user_eval_metric_id: "",
  custom_eval_config_id: "",
  experiment_id: "",
  feedback_improvement: "",
  ...overrides,
});

const rows = {
  observe: row({
    id: "observe-1",
    source: "observe",
    source_id: "span-1",
    custom_eval_config_id: "cfg-1",
    explanation: EVAL_EXPLANATION,
    feedback_improvement: OBSERVE_NOTE,
  }),
  observeEdited: row({
    id: "observe-edited",
    source: "observe",
    source_id: "span-2",
    custom_eval_config_id: "cfg-1",
    explanation: "Edited note",
    feedback_improvement: "Edited note",
  }),
  prototype: row({
    id: "prototype-1",
    source: "experiment",
    source_id: "span-3",
    custom_eval_config_id: "cfg-2",
    explanation: EVAL_EXPLANATION,
    feedback_improvement: "Prototype run note",
  }),
  dataset: row({
    id: "dataset-1",
    source: "dataset",
    source_id: "column-1",
    user_eval_metric_id: "metric-1",
    explanation: DATASET_NOTE,
  }),
};

let queryClient;

function renderTab() {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <EvalFeedbackTab templateId="template-1" />
    </QueryClientProvider>,
  );
}

function openRow(id) {
  fireEvent.click(screen.getByRole("button", { name: `open ${id}` }));
}

describe("Feedback tab note", () => {
  beforeEach(() => {
    mocks.items = Object.values(rows);
    mocks.drawerProps = null;
  });

  afterEach(() => {
    cleanup();
    queryClient?.clear();
  });

  it("lists the person's note for Observe and prototype-run feedback, not the eval's explanation", () => {
    renderTab();

    expect(screen.getByTestId("note-observe-1")).toHaveTextContent(
      OBSERVE_NOTE,
    );
    expect(screen.getByTestId("note-prototype-1")).toHaveTextContent(
      "Prototype run note",
    );
    expect(screen.queryByText(EVAL_EXPLANATION)).not.toBeInTheDocument();
    expect(screen.getByTestId("note-dataset-1")).toHaveTextContent(
      DATASET_NOTE,
    );
  });

  it("shows the person's note in the details panel of Observe feedback", () => {
    renderTab();
    openRow("observe-1");

    expect(screen.getByText("Improvement Note")).toBeInTheDocument();
    expect(screen.getAllByText(OBSERVE_NOTE)).toHaveLength(2);
    expect(screen.queryByText(EVAL_EXPLANATION)).not.toBeInTheDocument();
  });

  it("finds Observe feedback by the person's note", () => {
    renderTab();
    fireEvent.change(screen.getByLabelText("Search this page"), {
      target: { value: "pricing page" },
    });

    expect(screen.getByTestId("row-observe-1")).toBeInTheDocument();
    expect(screen.queryByTestId("row-dataset-1")).not.toBeInTheDocument();
  });

  it("does not hand the person's own note to Edit Feedback as the eval's explanation", () => {
    renderTab();
    openRow("dataset-1");

    expect(mocks.drawerProps.existingFeedback.id).toBe("dataset-1");
    expect(mocks.drawerProps.output.reason).toBe("");
  });

  it("hands Edit Feedback the eval's explanation that Observe stored", () => {
    renderTab();
    openRow("observe-1");

    expect(mocks.drawerProps.output.reason).toBe(EVAL_EXPLANATION);
  });

  it("hands no explanation once an edit has replaced it with the person's note", () => {
    renderTab();
    openRow("observe-edited");

    expect(mocks.drawerProps.output.reason).toBe("");
  });
});
