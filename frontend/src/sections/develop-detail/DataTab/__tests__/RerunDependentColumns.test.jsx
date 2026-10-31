import React from "react";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import RerunDependentColumns from "../RerunDependentColumns";
import { useRerunDependentColumnsStore } from "../../states";

const mocks = vi.hoisted(() => ({
  enqueueSnackbar: vi.fn(),
  getColumns: vi.fn(),
  getQueryOptions: vi.fn(),
  post: vi.fn(),
  refreshGrid: vi.fn(),
}));

vi.mock("src/components/snackbar", () => ({
  enqueueSnackbar: mocks.enqueueSnackbar,
}));

vi.mock("src/api/develop/develop-detail", () => ({
  getDatasetQueryOptions: (...args) => {
    mocks.getQueryOptions(...args);
    return { queryFn: mocks.getColumns };
  },
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

const completedColumns = {
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
};

describe("RerunDependentColumns", () => {
  beforeEach(() => {
    mocks.enqueueSnackbar.mockReset();
    mocks.getColumns.mockReset();
    mocks.getQueryOptions.mockReset();
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
    mocks.getColumns.mockResolvedValue(completedColumns);
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
      expect(mocks.enqueueSnackbar).toHaveBeenCalledWith(
        "Dependent columns rerun successfully",
        { variant: "success" },
      ),
    );
    // Once after the dependent starts, so the grid shows it running, and
    // once when the chain is done.
    expect(mocks.refreshGrid).toHaveBeenCalledTimes(2);
  });

  it("reads a single table row per completion poll", async () => {
    mocks.getColumns.mockResolvedValue(completedColumns);
    mocks.post.mockResolvedValue({ data: { status: true } });
    openDialog();
    render(<RerunDependentColumns dataset="dataset-id" />);

    fireEvent.click(screen.getByRole("button", { name: "Rerun selected" }));

    await waitFor(() =>
      expect(mocks.enqueueSnackbar).toHaveBeenCalledWith(
        "Dependent columns rerun successfully",
        { variant: "success" },
      ),
    );
    // One poll for the source column and one for the dependent.
    expect(mocks.getQueryOptions).toHaveBeenCalledTimes(2);
    mocks.getQueryOptions.mock.calls.forEach((args) =>
      expect(args).toEqual(["dataset-id", 0, [], [], "", { pageSize: 1 }]),
    );
  });

  it("closes once confirmed and finishes the chain in the background", async () => {
    let finishSource;
    mocks.getColumns
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishSource = resolve;
          }),
      )
      .mockResolvedValue(completedColumns);
    mocks.post.mockResolvedValue({ data: { status: true } });
    openDialog();
    render(<RerunDependentColumns dataset="dataset-id" />);

    fireEvent.click(screen.getByRole("button", { name: "Rerun selected" }));

    // The source column is still running, and the page is no longer blocked.
    expect(
      useRerunDependentColumnsStore.getState().rerunDependentColumns,
    ).toBeNull();
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(mocks.enqueueSnackbar).toHaveBeenCalledWith(
      "Selected columns will rerun after Source finishes",
      { variant: "info" },
    );
    expect(mocks.post).not.toHaveBeenCalled();

    finishSource(completedColumns);

    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith(
        "/model-hub/columns/dependent-id/rerun-operation/",
        { operation_type: "classify" },
      ),
    );
    expect(mocks.enqueueSnackbar).toHaveBeenCalledWith("Rerunning Dependent", {
      variant: "info",
    });
    await waitFor(() =>
      expect(mocks.enqueueSnackbar).toHaveBeenCalledWith(
        "Dependent columns rerun successfully",
        { variant: "success" },
      ),
    );
  });

  it("reports an error when a dependent rerun fails", async () => {
    mocks.getColumns.mockResolvedValue(completedColumns);
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
    expect(mocks.enqueueSnackbar).not.toHaveBeenCalledWith(
      "Dependent columns rerun successfully",
      { variant: "success" },
    );
    expect(mocks.refreshGrid).not.toHaveBeenCalled();
  });
});
