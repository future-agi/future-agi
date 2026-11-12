import React from "react";
import { Controller } from "react-hook-form";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import EvalUsageTab from "../EvalUsageTab";

const h = vi.hoisted(() => ({
  chart: {},
  logs: {},
  readLogs: vi.fn(),
  refreshChart: vi.fn(),
  refreshLogs: vi.fn(),
  post: vi.fn(),
  config: { output: "Pass/Fail" },
}));

// Keep the real detail subtree, row normalization, feedback drawer, form state,
// validation and request construction. Only transport and presentation are mocked.
/* eslint-disable react/prop-types */
vi.mock("../../hooks/useEvalUsage", () => ({
  useEvalUsageChart: () => h.chart,
  useEvalUsageLogs: (...args) => h.readLogs(...args),
}));
vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
  useQuery: () => ({ data: h.config, isPending: false }),
  useMutation: ({ mutationFn }) => ({
    mutate: mutationFn,
    isPending: false,
  }),
}));
vi.mock("src/utils/axios", () => ({
  default: { post: h.post },
  endpoints: {
    develop: {
      eval: {
        getEvalConfigs: "/eval-config",
        addEvalsFeedback: "/eval-feedback",
      },
    },
  },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("@mui/material", async (importOriginal) => ({
  ...(await importOriginal()),
  // Remove animation/portal timing, not child state or identity.
  Slide: ({ in: open, children }) => (open ? children : null),
  Drawer: ({ open, children }) =>
    open ? <section aria-label="Feedback draft">{children}</section> : null,
}));
vi.mock("src/sections/projects/DateTimeRangePicker", () => ({
  default: ({ setDateOption }) => (
    <button type="button" onClick={() => setDateOption("7D")}>
      Last 7 days
    </button>
  ),
}));
vi.mock("../UsageChart", () => ({ default: () => null }));
vi.mock("src/components/data-table", () => ({
  DataTable: ({ data, onRowClick }) => (
    <div>
      {data.map((row) => (
        <button key={row.id} type="button" onClick={() => onRowClick(row)}>
          Open {row.id}
        </button>
      ))}
    </div>
  ),
  DataTablePagination: ({ page, onPageChange, onPageSizeChange }) => (
    <div>
      <button type="button" onClick={() => onPageChange(page + 1)}>
        Next page
      </button>
      <button type="button" onClick={() => onPageSizeChange(50)}>
        Show 50 rows
      </button>
    </div>
  ),
}));
vi.mock("src/components/FormSearchField/FormSearchField", () => ({
  default: ({ value, onChange }) => (
    <input aria-label="Search history" value={value} onChange={onChange} />
  ),
}));
vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => (
    <span
      aria-hidden="true"
      data-testid={
        icon.includes("arrow-up")
          ? "previous-run"
          : icon.includes("arrow-down")
            ? "next-run"
            : icon
      }
    />
  ),
}));
vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));
vi.mock("src/components/ColumnDropdown/ColumnDropdown", () => ({
  default: () => null,
}));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("@monaco-editor/react", () => ({ default: () => null }));
vi.mock("src/sections/common/EvalsTasks/PartialInputWarningDetails", () => ({
  default: () => null,
}));
vi.mock("src/hooks/use-debounce", () => ({ useDebounce: (value) => value }));
vi.mock("src/hooks/use-local-storage", () => ({
  getStorage: () => null,
  setStorage: vi.fn(),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "editor" }),
}));
vi.mock("src/utils/rolePermissionMapping", () => ({
  PERMISSIONS: { EDIT_CREATE_DELETE_EVALS: "edit" },
  RolePermission: { EVALS: { edit: { editor: true } } },
}));
vi.mock(
  "src/sections/evals/EvalDetails/EvalsFeedback/AddEvalsFeedbackForm",
  () => ({
    default: ({ control, handleSubmit, handleSubmitForm, disabled }) => (
      <form onSubmit={handleSubmit(handleSubmitForm)}>
        <Controller
          name="value"
          control={control}
          render={({ field }) => <input aria-label="Rating" {...field} />}
        />
        <Controller
          name="explanation"
          control={control}
          render={({ field }) => (
            <textarea aria-label="Feedback explanation" {...field} />
          )}
        />
        <Controller
          name="actionType"
          control={control}
          render={({ field }) => (
            <select aria-label="Feedback action" {...field}>
              <option value="">Choose action</option>
              <option value="retune">Re-tune</option>
            </select>
          )}
        />
        <button type="submit" disabled={disabled}>
          Re-tune
        </button>
      </form>
    ),
  }),
);
/* eslint-enable react/prop-types */

// Raw usage-table fixtures: normalizeRow must supply the stable log ID.
const A = {
  row_id: "run-a",
  source: { cell_value: "eval_playground" },
  status: { cell_value: "success" },
  input: { cell_value: "Only A" },
  result: { cell_value: "Passed" },
  detail: { cell_value: { output: { reason: "Original explanation A" } } },
};
const B = {
  ...A,
  row_id: "run-b",
  input: { cell_value: "Only B" },
  detail: { cell_value: { output: { reason: "Original explanation B" } } },
};
const DRAFT_A = "This feedback belongs only to run A";

function exactLogs(rows) {
  return {
    data: { table: rows, pagination: { total: 60 } },
    isLoading: false,
    isFetching: false,
    isError: false,
    refresh: h.refreshLogs,
  };
}

function renderUsage(rows = [A]) {
  h.logs = exactLogs(rows);
  return render(<EvalUsageTab templateId="eval-1" />);
}

function openRun(id = "run-a") {
  fireEvent.click(screen.getByRole("button", { name: `Open ${id}` }));
}

async function fillDraft(explanation = DRAFT_A) {
  fireEvent.change(screen.getByLabelText("Rating"), {
    target: { value: "passed" },
  });
  fireEvent.change(screen.getByLabelText("Feedback explanation"), {
    target: { value: explanation },
  });
  fireEvent.change(screen.getByLabelText("Feedback action"), {
    target: { value: "retune" },
  });
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Re-tune" })).toBeEnabled(),
  );
}

async function openDraft() {
  fireEvent.click(screen.getByRole("button", { name: "Add Feedback" }));
  await fillDraft();
}

function beginRefresh(view) {
  let resolve;
  const pending = new Promise((done) => {
    resolve = done;
  });
  h.refreshLogs.mockImplementationOnce(() => {
    h.logs = {
      ...h.logs,
      data: { queryPending: true, queryRefreshing: true },
      isFetching: true,
    };
    return pending.then((rows) => {
      h.logs = exactLogs(rows);
      view.rerender(<EvalUsageTab templateId="eval-1" />);
    });
  });
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  view.rerender(<EvalUsageTab templateId="eval-1" />);
  expect(h.refreshLogs).toHaveBeenCalledOnce();
  expect(screen.getByRole("button", { name: "Refreshing" })).toBeDisabled();
  return async (rows) => {
    await act(async () => {
      resolve(rows);
      await pending;
    });
  };
}

async function submitFor(id, explanation = DRAFT_A) {
  fireEvent.click(screen.getByRole("button", { name: "Re-tune" }));
  await waitFor(() => expect(h.post).toHaveBeenCalledOnce());
  expect(h.post).toHaveBeenCalledWith("/eval-feedback", {
    log_id: id,
    value: "passed",
    explanation,
    action_type: "retune",
  });
}

function expectClosed() {
  expect(screen.queryByLabelText("Feedback explanation")).not.toBeInTheDocument();
  expect(h.post).not.toHaveBeenCalled();
}

async function expectFreshDraftForB() {
  fireEvent.click(screen.getByRole("button", { name: "Add Feedback" }));
  expect(screen.getByLabelText("Feedback explanation")).toHaveValue("");
  expect(screen.getByLabelText("Rating")).toHaveValue("");
  expect(screen.getByRole("button", { name: "Re-tune" })).toBeDisabled();
  await fillDraft("New explicit feedback for B");
  await submitFor("run-b", "New explicit feedback for B");
}

describe("W3-A-003 usage feedback identity", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    h.refreshLogs.mockReset();
    h.post.mockResolvedValue({ data: { result: {} } });
    h.readLogs.mockImplementation(() => h.logs);
    h.chart = {
      data: { stats: {}, chart: [] },
      isLoading: false,
      isFetching: false,
      refresh: h.refreshChart,
    };
  });

  it("R1 keeps A's pending-refresh draft attached to A after B is inserted", async () => {
    const view = renderUsage();
    openRun();
    const finish = beginRefresh(view);
    // Open feedback while the retained exact [A] is still displayed.
    expect(screen.getByRole("button", { name: "Open run-a" })).toBeInTheDocument();
    await openDraft();
    await finish([B, { ...A }]);
    expect(screen.getByLabelText("Feedback explanation")).toHaveValue(DRAFT_A);
    await submitFor("run-a");
    expect(screen.getByText("2 / 2")).toBeInTheDocument();
  });

  it("P1 preserves valid feedback when the same run refreshes", async () => {
    const view = renderUsage();
    openRun();
    const finish = beginRefresh(view);
    await openDraft();
    await finish([{ ...A }]);
    await submitFor("run-a");
  });

  it("R2 closes feedback when a refresh removes A and requires explicit selection", async () => {
    const view = renderUsage();
    openRun();
    const finish = beginRefresh(view);
    await openDraft();
    await finish([B]);
    expectClosed();
    openRun("run-b");
    await expectFreshDraftForB();
  });

  it("R3 keeps A selected when filtering changes its position", async () => {
    renderUsage([B, A]);
    openRun();
    await openDraft();
    fireEvent.change(screen.getByLabelText("Search history"), {
      target: { value: "Only A" },
    });
    expect(screen.getByLabelText("Feedback explanation")).toHaveValue(DRAFT_A);
    await submitFor("run-a");
  });

  it("R4 closes a filtered-out draft and does not resurrect it when search clears", async () => {
    renderUsage([A, B]);
    openRun();
    await openDraft();
    fireEvent.change(screen.getByLabelText("Search history"), {
      target: { value: "Only B" },
    });
    expectClosed();
    fireEvent.change(screen.getByLabelText("Search history"), {
      target: { value: "" },
    });
    expectClosed();
    expect(screen.queryByRole("button", { name: "Add Feedback" })).not.toBeInTheDocument();
    openRun("run-b");
    await expectFreshDraftForB();
  });

  it.each(["Next page", "Show 50 rows", "Last 7 days"])(
    "R5 never transfers a draft to replacement results after %s",
    async (control) => {
      const view = renderUsage();
      openRun();
      await openDraft();
      fireEvent.click(screen.getByRole("button", { name: control }));
      // Like keepPreviousData, leave A visible until the new exact page arrives.
      h.logs = exactLogs([B]);
      view.rerender(<EvalUsageTab templateId="eval-1" />);
      expectClosed();
      openRun("run-b");
      await expectFreshDraftForB();
    },
  );

  it.each(["next", "previous", "j", "k", "row"])(
    "R6 resets feedback on explicit selection via %s",
    async (navigation) => {
      renderUsage(["previous", "k"].includes(navigation) ? [B, A] : [A, B]);
      openRun();
      await openDraft();
      if (["j", "k"].includes(navigation)) {
        fireEvent.keyDown(window, { key: navigation });
      } else if (navigation === "row") {
        openRun("run-b");
      } else {
        fireEvent.click(screen.getByTestId(`${navigation}-run`).closest("button"));
      }
      expectClosed();
      await expectFreshDraftForB();
    },
  );
});
