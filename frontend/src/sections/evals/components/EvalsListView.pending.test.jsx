/* eslint-disable react/prop-types */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  screen,
  within,
} from "@testing-library/react";
import { render } from "src/utils/test-utils";
import EvalsListView from "./EvalsListView";

const state = vi.hoisted(() => ({
  bulkDelete: { mutateAsync: vi.fn(async () => ({})), isPending: false },
  list: {
    items: [{ id: "eval-1", name: "User evaluation", owner: "user" }],
    total: 1,
  },
}));
vi.mock("../hooks/useEvalsList", () => ({
  useEvalsList: () => ({ data: state.list, isLoading: false }),
  useEvalsListCharts: () => ({ data: null, isLoading: false }),
  useBulkDeleteEvals: () => state.bulkDelete,
}));
vi.mock("@tanstack/react-query", async (importOriginal) => ({
  ...(await importOriginal()),
  useQuery: () => ({ data: [] }),
}));
vi.mock("src/utils/axios", () => ({
  default: {
    post: vi.fn(() => {
      throw new Error("Unexpected HTTP call in eval list pending test");
    }),
  },
  endpoints: { develop: { eval: { getEvalNames: "/eval-names/" } } },
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
// Reuse TaskListView.pagination's controlled selection seam; keep the real
// BulkActionsBar and BulkDeleteDialog so the producer's loading prop is tested.
vi.mock("src/components/data-table", () => ({
  DataTable: ({ data, onRowSelectionChange }) => (
    <button
      type="button"
      disabled={!data.length}
      onClick={() => onRowSelectionChange({ 0: true })}
    >
      Select first evaluation
    </button>
  ),
  DataTablePagination: () => null,
}));
vi.mock("src/components/filter-panel/FilterPanel", () => ({
  default: () => null,
}));
vi.mock("./DisplayOptionsPopover", () => ({
  default: () => null,
  DEFAULT_COLUMN_ORDER: ["name"],
}));
vi.mock("src/sections/project/VolumeBarChart", () => ({ default: () => null }));
vi.mock("./ErrorRateSparkline", () => ({ default: () => null }));

beforeEach(() => {
  state.bulkDelete.isPending = false;
  state.bulkDelete.mutateAsync.mockClear();
  localStorage.clear();
});
afterEach(cleanup);

describe("eval bulk-delete pending state", () => {
  it("disables Delete and Cancel and shows Deleting... while bulkDelete is pending", async () => {
    const { rerender } = render(<EvalsListView />);
    fireEvent.click(
      screen.getByRole("button", { name: "Select first evaluation" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = screen.getByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Delete" });
    const cancel = within(dialog).getByRole("button", { name: "Cancel" });
    expect(confirm).toBeEnabled();
    expect(cancel).toBeEnabled();
    expect(state.bulkDelete).not.toHaveProperty("isLoading");

    state.bulkDelete.isPending = true;
    rerender(<EvalsListView />);
    expect(confirm).toBeDisabled();
    expect(confirm).toHaveTextContent(/^Deleting\.\.\.$/);
    expect(cancel).toBeDisabled();
    fireEvent.click(confirm);
    fireEvent.click(cancel);
    expect(state.bulkDelete.mutateAsync).not.toHaveBeenCalled();
    expect(dialog).toBeInTheDocument();

    state.bulkDelete.isPending = false;
    rerender(<EvalsListView />);
    expect(confirm).toBeEnabled();
    expect(confirm).toHaveTextContent(/^Delete$/);
    expect(cancel).toBeEnabled();
    await act(async () => {
      fireEvent.click(confirm);
    });
    expect(state.bulkDelete.mutateAsync).toHaveBeenCalledExactlyOnceWith([
      "eval-1",
    ]);
    expect(
      await screen.findByRole("button", { name: "Create evals" }),
    ).toBeInTheDocument();
  });
});
