import React from "react";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import RerunDependentColumns from "../RerunDependentColumns";
import { useRerunDependentColumnsStore } from "../../states";

const mocks = vi.hoisted(() => ({
  enqueueSnackbar: vi.fn(),
  getColumns: vi.fn(),
  post: vi.fn(),
  refreshGrid: vi.fn(),
}));

vi.mock("src/components/snackbar", () => ({
  enqueueSnackbar: mocks.enqueueSnackbar,
}));

vi.mock("src/api/develop/develop-detail", () => ({
  getDatasetQueryOptions: () => ({
    queryFn: mocks.getColumns,
  }),
}));

vi.mock("src/utils/axios", () => ({
  default: { post: mocks.post },
  endpoints: {
    develop: {
      addColumns: {
        updateDynamicColumn: (columnId) =>
          `/model-hub/columns/${columnId}/rerun-operation/`,
      },
    },
  },
}));

vi.mock("../../Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({
    refreshGrid: mocks.refreshGrid,
  }),
}));

const openDialog = () => {
  useRerunDependentColumnsStore.getState().setRerunDependentColumns({
    sourceColumn: { id: "source-id", name: "Source" },
    dependents: [
      {
        id: "dependent-id",
        name: "Dependent",
        operationType: "classify",
        dependencyIds: ["source-id"],
      },
    ],
  });
};

describe("RerunDependentColumns", () => {
  beforeEach(() => {
    mocks.enqueueSnackbar.mockReset();
    mocks.getColumns.mockReset();
    mocks.post.mockReset();
    mocks.refreshGrid.mockReset();
    useRerunDependentColumnsStore.getState().setRerunDependentColumns(null);
  });

  it("closes without starting dependent reruns when skipped", () => {
    openDialog();
    render(<RerunDependentColumns dataset="dataset-id" />);

    expect(screen.getByRole("checkbox", { name: "Dependent" })).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Skip" }));

    expect(mocks.post).not.toHaveBeenCalled();
    expect(
      useRerunDependentColumnsStore.getState().rerunDependentColumns,
    ).toBeNull();
  });

  it("reruns a selected dependent with its operation type", async () => {
    mocks.getColumns.mockResolvedValue({
      data: {
        result: {
          column_config: [
            { id: "source-id", name: "Source", status: "Completed" },
            {
              id: "dependent-id",
              name: "Dependent",
              status: "Completed",
            },
          ],
        },
      },
    });
    mocks.post.mockResolvedValue({ data: { status: true } });
    openDialog();
    render(<RerunDependentColumns dataset="dataset-id" />);

    fireEvent.click(screen.getByRole("button", { name: "Rerun selected" }));

    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith(
        "/model-hub/columns/dependent-id/rerun-operation/",
        { operation_type: "classify" },
      ),
    );
    await waitFor(() =>
      expect(
        useRerunDependentColumnsStore.getState().rerunDependentColumns,
      ).toBeNull(),
    );
    expect(mocks.refreshGrid).toHaveBeenCalledTimes(1);
  });

  it("keeps the dialog open and reports an error when a rerun fails", async () => {
    mocks.getColumns.mockResolvedValue({
      data: {
        result: {
          column_config: [
            { id: "source-id", name: "Source", status: "Completed" },
            {
              id: "dependent-id",
              name: "Dependent",
              status: "Completed",
            },
          ],
        },
      },
    });
    mocks.post.mockRejectedValue({
      response: { data: { message: "Dependent rerun failed" } },
    });
    openDialog();
    render(<RerunDependentColumns dataset="dataset-id" />);

    fireEvent.click(screen.getByRole("button", { name: "Rerun selected" }));

    await waitFor(() =>
      expect(mocks.enqueueSnackbar).toHaveBeenCalledWith(
        "Dependent rerun failed",
        { variant: "error" },
      ),
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(
      useRerunDependentColumnsStore.getState().rerunDependentColumns,
    ).not.toBeNull();
    expect(mocks.refreshGrid).not.toHaveBeenCalled();
  });
});
