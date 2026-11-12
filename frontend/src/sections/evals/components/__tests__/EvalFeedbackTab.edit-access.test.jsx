/* eslint-disable react/prop-types */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// W10-EVALS-U01: Edit Feedback must not look available on rows the viewer
// cannot change, and must say why before they fill in the form.

const OWNER_ONLY = "Only the person who left this feedback can edit it.";
const EXPERIMENT_ONLY =
  "Edit this feedback from the experiment it was left on.";

const mocks = vi.hoisted(() => ({ items: [] }));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Admin", user: { id: "user-me" } }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("@monaco-editor/react", () => ({ default: () => null }));
vi.mock("src/components/FormSearchField/FormSearchField", () => ({
  default: () => null,
}));
vi.mock("src/sections/develop-detail/DataTab/common", () => ({
  normalizeEvalCellValue: (value) => value,
}));
vi.mock(
  "src/sections/evals/EvalDetails/EvalsFeedback/AddEvalsFeedbackDrawer",
  () => ({ default: () => null }),
);
// The grid itself is covered elsewhere; a plain list keeps row clicks direct.
vi.mock("src/components/data-table", () => ({
  DataTable: ({ data, onRowClick }) => (
    <ul>
      {data.map((row) => (
        <li key={row.id}>
          <button type="button" onClick={() => onRowClick(row)}>
            {`open ${row.id}`}
          </button>
        </li>
      ))}
    </ul>
  ),
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

const row = (overrides) => ({
  value: "passed",
  explanation: "Note",
  action_type: "retune",
  user_name: "Someone",
  created_at: "2026-11-01T10:00:00Z",
  user_eval_metric_id: "",
  custom_eval_config_id: "",
  experiment_id: "",
  ...overrides,
});

const rows = {
  observeByTeammate: row({
    id: "observe-teammate",
    source: "observe",
    source_id: "span-1",
    custom_eval_config_id: "cfg-1",
    user_id: "user-teammate",
  }),
  observeByMe: row({
    id: "observe-mine",
    source: "observe",
    source_id: "span-2",
    custom_eval_config_id: "cfg-1",
    user_id: "user-me",
  }),
  prototypeByTeammate: row({
    id: "prototype-teammate",
    source: "experiment",
    source_id: "span-3",
    custom_eval_config_id: "cfg-2",
    user_id: "user-teammate",
  }),
  experimentWithoutRun: row({
    id: "experiment-unlinked",
    source: "experiment",
    source_id: "column-9",
    user_eval_metric_id: "metric-9",
    user_id: "user-me",
  }),
  datasetByTeammate: row({
    id: "dataset-teammate",
    source: "dataset",
    source_id: "column-1",
    user_eval_metric_id: "metric-1",
    user_id: "user-teammate",
  }),
};

let queryClient;

function openRow(id) {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <EvalFeedbackTab templateId="template-1" />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: `open ${id}` }));
  return screen.getByRole("button", { name: "Edit Feedback" });
}

describe("Feedback tab Edit Feedback availability", () => {
  beforeEach(() => {
    mocks.items = Object.values(rows);
  });

  afterEach(() => {
    cleanup();
    queryClient?.clear();
  });

  it("disables Edit Feedback on a teammate's Observe feedback and says why", () => {
    const edit = openRow("observe-teammate");

    expect(edit).toBeDisabled();
    expect(screen.getByText(OWNER_ONLY)).toBeInTheDocument();
  });

  it("disables Edit Feedback on a teammate's prototype-run feedback", () => {
    const edit = openRow("prototype-teammate");

    expect(edit).toBeDisabled();
    expect(screen.getByText(OWNER_ONLY)).toBeInTheDocument();
  });

  it("disables Edit Feedback on experiment feedback with no experiment to save to", () => {
    const edit = openRow("experiment-unlinked");

    expect(edit).toBeDisabled();
    expect(screen.getByText(EXPERIMENT_ONLY)).toBeInTheDocument();
  });

  it("keeps Edit Feedback available on the viewer's own Observe feedback", () => {
    const edit = openRow("observe-mine");

    expect(edit).toBeEnabled();
    expect(screen.queryByText(OWNER_ONLY)).not.toBeInTheDocument();
  });

  it("keeps Edit Feedback available on a teammate's dataset feedback", () => {
    const edit = openRow("dataset-teammate");

    expect(edit).toBeEnabled();
    expect(screen.queryByText(OWNER_ONLY)).not.toBeInTheDocument();
    expect(screen.queryByText(EXPERIMENT_ONLY)).not.toBeInTheDocument();
  });
});
