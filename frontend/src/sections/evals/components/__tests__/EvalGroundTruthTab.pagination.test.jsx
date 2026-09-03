import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { INTERACTIVE_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";

const mocks = vi.hoisted(() => ({
  enqueueSnackbar: vi.fn(),
  getDatasetPage: vi.fn(),
  upload: vi.fn(),
}));

vi.mock("notistack", () => ({
  useSnackbar: () => ({ enqueueSnackbar: mocks.enqueueSnackbar }),
}));

vi.mock("react-dropzone", () => ({
  useDropzone: () => ({
    getRootProps: () => ({}),
    getInputProps: () => ({}),
    isDragActive: false,
  }),
}));

vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({
    data: [{ id: "dataset-1", name: "Reference data", row_count: 101 }],
    isLoading: false,
  }),
  useGetDatasetColumns: () => ({
    data: [{ id: "column-1", name: "answer" }],
  }),
  useGetDatasetDetail: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => mocks.getDatasetPage(...args) },
}));

vi.mock("../../hooks/useGroundTruth", () => ({
  useUploadGroundTruth: () => ({
    mutateAsync: mocks.upload,
    isPending: false,
  }),
  useDeleteGroundTruth: vi.fn(),
  useGroundTruthData: vi.fn(),
  useGroundTruthList: vi.fn(),
  useGroundTruthStatus: vi.fn(),
  useSaveGroundTruthSetup: vi.fn(),
  useTriggerEmbedding: vi.fn(),
}));

import { UploadDrawer } from "../EvalGroundTruthTab";

const tableRows = (start, count) =>
  Array.from({ length: count }, (_, index) => ({
    row_id: `row-${start + index}`,
    "column-1": { cell_value: `answer-${start + index}` },
  }));

const pageResponse = ({ pageIndex, rows, hasMore, nextPageIndex }) => ({
  data: {
    result: {
      metadata: {
        dataset_name: "Reference data",
        total_rows: 101,
        total_pages: 2,
        page_size: 100,
        current_page_index: pageIndex,
        has_more: hasMore,
        next_page_index: nextPageIndex,
        next_cursor: hasMore ? `signed-cursor-${pageIndex + 1}` : null,
        is_exact: true,
        snapshot_bound: true,
        error_messages: [],
      },
      column_config: [{ id: "column-1", name: "answer" }],
      table: rows,
    },
  },
});

describe("EvalGroundTruth existing-dataset import", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getDatasetPage
      .mockResolvedValueOnce(
        pageResponse({
          pageIndex: 0,
          rows: tableRows(0, 100),
          hasMore: true,
          nextPageIndex: 1,
        }),
      )
      .mockResolvedValueOnce(
        pageResponse({
          pageIndex: 1,
          rows: tableRows(100, 1),
          hasMore: false,
          nextPageIndex: null,
        }),
      );
    mocks.upload.mockResolvedValue({ id: "ground-truth-1" });
  });

  it("loads one 100-row page per click and uploads only after exact exhaustion", async () => {
    render(
      <UploadDrawer
        open
        onClose={vi.fn()}
        templateId="template-1"
        evalVariables={[]}
      />,
    );

    fireEvent.click(screen.getByText("Choose from existing dataset"));
    fireEvent.click(await screen.findByText("Reference data"));

    fireEvent.click(screen.getByRole("button", { name: "Load rows" }));
    await screen.findByText("100 of 101 rows loaded");
    expect(mocks.upload).not.toHaveBeenCalled();
    expect(mocks.getDatasetPage).toHaveBeenNthCalledWith(
      1,
      expect.stringContaining("/get-dataset-table/"),
      expect.objectContaining({
        params: {
          current_page_index: 0,
          page_size: 100,
          exact_snapshot: true,
        },
        signal: expect.any(AbortSignal),
        timeout: INTERACTIVE_REQUEST_TIMEOUT_MS,
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    await screen.findByText("101 of 101 rows loaded");
    expect(screen.getByText("Complete")).toBeInTheDocument();
    expect(mocks.getDatasetPage).toHaveBeenNthCalledWith(
      2,
      expect.stringContaining("/get-dataset-table/"),
      expect.objectContaining({
        params: {
          current_page_index: 1,
          page_size: 100,
          exact_snapshot: true,
          cursor: "signed-cursor-1",
        },
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Import" }));
    await waitFor(() => expect(mocks.upload).toHaveBeenCalledTimes(1));
    expect(mocks.upload).toHaveBeenCalledWith(
      expect.objectContaining({
        columns: ["answer"],
        data: expect.arrayContaining([
          { answer: "answer-0" },
          { answer: "answer-100" },
        ]),
      }),
    );
    expect(mocks.upload.mock.calls[0][0].data).toHaveLength(101);
  });

  it("keeps the loaded prefix when the signed continuation is rejected", async () => {
    mocks.getDatasetPage
      .mockReset()
      .mockResolvedValueOnce(
        pageResponse({
          pageIndex: 0,
          rows: tableRows(0, 100),
          hasMore: true,
          nextPageIndex: 1,
        }),
      )
      .mockRejectedValueOnce(
        Object.assign(new Error("Request failed with status code 409"), {
          response: {
            status: 409,
            data: {
              code: "dataset_snapshot_changed",
              message: "The dataset changed. Restart the import.",
            },
          },
        }),
      );

    render(
      <UploadDrawer
        open
        onClose={vi.fn()}
        templateId="template-1"
        evalVariables={[]}
      />,
    );

    fireEvent.click(screen.getByText("Choose from existing dataset"));
    fireEvent.click(await screen.findByText("Reference data"));
    fireEvent.click(screen.getByRole("button", { name: "Load rows" }));
    await screen.findByText("100 of 101 rows loaded");

    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    await screen.findByText("The dataset changed. Restart the import.");

    expect(screen.getByText("100 of 101 rows loaded")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Load more" }),
    ).toBeInTheDocument();
    expect(mocks.upload).not.toHaveBeenCalled();
    expect(mocks.getDatasetPage).toHaveBeenNthCalledWith(
      2,
      expect.stringContaining("/get-dataset-table/"),
      expect.objectContaining({
        params: expect.objectContaining({
          current_page_index: 1,
          cursor: "signed-cursor-1",
        }),
      }),
    );
  });
});
