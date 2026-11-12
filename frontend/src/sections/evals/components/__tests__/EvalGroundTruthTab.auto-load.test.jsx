import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
    data: [{ id: "dataset-1", name: "Reference data", row_count: 250 }],
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

const TOTAL_ROWS = 250;
const PAGE_SIZE = 100;

const tableRows = (start, count) =>
  Array.from({ length: count }, (_, index) => ({
    row_id: `row-${start + index}`,
    "column-1": { cell_value: `answer-${start + index}` },
  }));

const page = (pageIndex) => {
  const start = pageIndex * PAGE_SIZE;
  const count = Math.min(PAGE_SIZE, TOTAL_ROWS - start);
  const hasMore = start + count < TOTAL_ROWS;
  return {
    data: {
      result: {
        metadata: {
          dataset_name: "Reference data",
          total_rows: TOTAL_ROWS,
          total_pages: Math.ceil(TOTAL_ROWS / PAGE_SIZE),
          page_size: PAGE_SIZE,
          current_page_index: pageIndex,
          has_more: hasMore,
          next_page_index: hasMore ? pageIndex + 1 : null,
          next_cursor: hasMore ? `signed-cursor-${pageIndex + 1}` : null,
          is_exact: true,
          snapshot_bound: true,
          error_messages: [],
        },
        column_config: [{ id: "column-1", name: "answer" }],
        table: tableRows(start, count),
      },
    },
  };
};

const pageRequest = (pageIndex) => [
  expect.stringContaining("/get-dataset-table/"),
  expect.objectContaining({
    params: {
      current_page_index: pageIndex,
      page_size: PAGE_SIZE,
      exact_snapshot: true,
      ...(pageIndex > 0 ? { cursor: `signed-cursor-${pageIndex}` } : {}),
    },
  }),
];

const openDatasetImport = async (onClose = vi.fn()) => {
  render(
    <UploadDrawer
      open
      onClose={onClose}
      templateId="template-1"
      evalVariables={[]}
    />,
  );
  fireEvent.click(screen.getByText("Choose from existing dataset"));
  fireEvent.click(await screen.findByText("Reference data"));
  return onClose;
};

describe("W10-EVALS-U03 ground truth import from a dataset", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getDatasetPage.mockReset();
    mocks.upload.mockReset();
    mocks.upload.mockResolvedValue({ id: "ground-truth-1" });
  });

  it("loads every page and imports from one Import click", async () => {
    mocks.getDatasetPage
      .mockResolvedValueOnce(page(0))
      .mockResolvedValueOnce(page(1))
      .mockResolvedValueOnce(page(2));
    const onClose = await openDatasetImport();

    expect(
      screen.queryByRole("button", { name: "Load rows" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Import" }));

    await waitFor(() => expect(mocks.upload).toHaveBeenCalledTimes(1));
    expect(mocks.getDatasetPage).toHaveBeenCalledTimes(3);
    expect(mocks.getDatasetPage).toHaveBeenNthCalledWith(1, ...pageRequest(0));
    expect(mocks.getDatasetPage).toHaveBeenNthCalledWith(2, ...pageRequest(1));
    expect(mocks.getDatasetPage).toHaveBeenNthCalledWith(3, ...pageRequest(2));
    expect(mocks.upload.mock.invocationCallOrder[0]).toBeGreaterThan(
      mocks.getDatasetPage.mock.invocationCallOrder[2],
    );
    const payload = mocks.upload.mock.calls[0][0];
    expect(payload.columns).toEqual(["answer"]);
    expect(payload.data).toHaveLength(TOTAL_ROWS);
    expect(payload.data[TOTAL_ROWS - 1]).toEqual({ answer: "answer-249" });
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });

  it("stops at a failed page, keeps the loaded rows and retries from the next row", async () => {
    mocks.getDatasetPage.mockResolvedValueOnce(page(0)).mockRejectedValueOnce(
      Object.assign(new Error("Request failed with status code 503"), {
        response: {
          status: 503,
          data: {
            code: "dataset_snapshot_unavailable",
            message:
              "Dataset rows could not be read within the deadline. Retry.",
          },
        },
      }),
    );
    await openDatasetImport();

    fireEvent.click(screen.getByRole("button", { name: "Import" }));

    await screen.findByText(
      "Dataset rows could not be read within the deadline. Retry.",
    );
    expect(screen.getByText("100 of 250 rows loaded")).toBeInTheDocument();
    expect(mocks.upload).not.toHaveBeenCalled();
    const retry = screen.getByRole("button", { name: "Retry from row 101" });
    expect(retry).toBeEnabled();

    mocks.getDatasetPage
      .mockResolvedValueOnce(page(1))
      .mockResolvedValueOnce(page(2));
    fireEvent.click(retry);

    await waitFor(() => expect(mocks.upload).toHaveBeenCalledTimes(1));
    expect(mocks.getDatasetPage).toHaveBeenCalledTimes(4);
    expect(mocks.getDatasetPage).toHaveBeenNthCalledWith(3, ...pageRequest(1));
    expect(mocks.getDatasetPage).toHaveBeenNthCalledWith(4, ...pageRequest(2));
    expect(mocks.upload.mock.calls[0][0].data).toHaveLength(TOTAL_ROWS);
  });

  it("cancels row loading, keeps the loaded rows and resumes on the next Import", async () => {
    let finishSecondPage;
    mocks.getDatasetPage.mockResolvedValueOnce(page(0)).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishSecondPage = resolve;
        }),
    );
    await openDatasetImport();

    fireEvent.click(screen.getByRole("button", { name: "Import" }));
    await screen.findByText("Loading 100 of 250 rows...");
    await waitFor(() => expect(mocks.getDatasetPage).toHaveBeenCalledTimes(2));

    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));

    await screen.findByText("100 of 250 rows loaded");
    expect(mocks.getDatasetPage.mock.calls[1][1].signal.aborted).toBe(true);
    expect(screen.getByRole("button", { name: "Import" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Back" })).toBeInTheDocument();

    // A late answer for the canceled page is discarded.
    finishSecondPage(page(1));
    await new Promise((resolve) => globalThis.setTimeout(resolve, 0));
    expect(screen.getByText("100 of 250 rows loaded")).toBeInTheDocument();
    expect(mocks.upload).not.toHaveBeenCalled();
    expect(mocks.getDatasetPage).toHaveBeenCalledTimes(2);

    mocks.getDatasetPage
      .mockResolvedValueOnce(page(1))
      .mockResolvedValueOnce(page(2));
    fireEvent.click(screen.getByRole("button", { name: "Import" }));

    await waitFor(() => expect(mocks.upload).toHaveBeenCalledTimes(1));
    expect(mocks.getDatasetPage).toHaveBeenCalledTimes(4);
    expect(mocks.getDatasetPage).toHaveBeenNthCalledWith(3, ...pageRequest(1));
    expect(mocks.upload.mock.calls[0][0].data).toHaveLength(TOTAL_ROWS);
  });
});
