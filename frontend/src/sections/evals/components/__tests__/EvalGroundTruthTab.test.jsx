/* eslint-disable react/prop-types */
import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const {
  listHook,
  dataHook,
  statusHook,
  deleteHook,
  saveHook,
  embedHook,
  uploadHook,
  evalDetailHook,
  datasetListHook,
  datasetColumnsHook,
  enqueueSnackbarMock,
  axiosGetMock,
} = vi.hoisted(() => ({
  listHook: vi.fn(),
  dataHook: vi.fn(),
  statusHook: vi.fn(),
  deleteHook: vi.fn(),
  saveHook: vi.fn(),
  embedHook: vi.fn(),
  uploadHook: vi.fn(),
  evalDetailHook: vi.fn(),
  datasetListHook: vi.fn(),
  datasetColumnsHook: vi.fn(),
  enqueueSnackbarMock: vi.fn(),
  axiosGetMock: vi.fn(),
}));

vi.mock("../../hooks/useGroundTruth", () => ({
  useGroundTruthList: (...args) => listHook(...args),
  useGroundTruthData: (...args) => dataHook(...args),
  useGroundTruthStatus: (...args) => statusHook(...args),
  useDeleteGroundTruth: (...args) => deleteHook(...args),
  useSaveGroundTruthSetup: (...args) => saveHook(...args),
  useTriggerEmbedding: (...args) => embedHook(...args),
  useUploadGroundTruth: (...args) => uploadHook(...args),
}));

vi.mock("../../hooks/useEvalDetail", () => ({
  useEvalDetail: (...args) => evalDetailHook(...args),
}));

vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: (...args) => datasetListHook(...args),
  useGetDatasetColumns: (...args) => datasetColumnsHook(...args),
  useGetDatasetDetail: () => ({ data: undefined, isLoading: false }),
}));

vi.mock("src/hooks/use-ag-theme", () => ({ useAgTheme: () => ({}) }));

vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => axiosGetMock(...args) },
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Admin" }),
}));

vi.mock("notistack", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useSnackbar: () => ({ enqueueSnackbar: enqueueSnackbarMock }),
  };
});

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-testid="icon">{icon}</span>,
}));

vi.mock("ag-grid-react", () => ({
  AgGridReact: ({ columnDefs, rowData }) => (
    <div data-testid="gt-grid">
      {rowData.map((row, i) => (
        <div key={i} data-testid={`gt-grid-row-${i}`}>
          {columnDefs.map((col) => (
            <span key={col.field}>{String(row[col.field] ?? "")}</span>
          ))}
        </div>
      ))}
    </div>
  ),
}));

import { GROUND_TRUTH_DATASET_PAGE_SIZE } from "../ground_truth_dataset_pagination";
import EvalGroundTruthTab, {
  shouldTriggerEmbed,
} from "../EvalGroundTruthTab";

function renderTab(props = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  function Wrapper({ children }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  }
  Wrapper.propTypes = { children: PropTypes.node };
  return render(
    <Wrapper>
      <EvalGroundTruthTab templateId="tmpl-1" {...props} />
    </Wrapper>,
  );
}

const dataset = {
  id: "gt-1",
  name: "QA Reference Set",
  row_count: 42,
  columns: ["question", "answer", "reason"],
  variable_mapping: {},
  role_mapping: {},
  max_examples: 3,
  enabled: false,
  embedding_status: "completed",
  embeddings_stale: false,
};

const previewData = {
  columns: ["question", "answer", "reason"],
  rows: [{ question: "What is 2+2?", answer: "4", reason: "Arithmetic" }],
};

describe("EvalGroundTruthTab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    evalDetailHook.mockReturnValue({
      data: {
        id: "tmpl-1",
        config: { required_keys: [], rule_prompt: "", output_type_normalized: "pass_fail" },
      },
    });
    dataHook.mockReturnValue({ data: undefined });
    statusHook.mockReturnValue({ data: undefined });
    deleteHook.mockReturnValue({ mutateAsync: vi.fn(), isPending: false });
    saveHook.mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({}),
      isPending: false,
      isError: false,
    });
    embedHook.mockReturnValue({ mutateAsync: vi.fn(), isPending: false });
    uploadHook.mockReturnValue({ mutateAsync: vi.fn(), isPending: false });
    datasetListHook.mockReturnValue({ data: [], isLoading: false });
    datasetColumnsHook.mockReturnValue({ data: undefined });
  });

  it("shows a loading spinner while the ground truth list is loading", () => {
    listHook.mockReturnValue({ data: undefined, isLoading: true });

    renderTab();

    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    expect(screen.queryByText("Add ground truth dataset")).not.toBeInTheDocument();
  });

  it("shows the empty state and opens the upload drawer on click", async () => {
    listHook.mockReturnValue({ data: { items: [] }, isLoading: false });

    renderTab();

    expect(screen.getByText("Add ground truth dataset")).toBeInTheDocument();

    await userEvent.click(screen.getByText("Add ground truth dataset"));

    expect(await screen.findByText("Add Ground Truth")).toBeInTheDocument();
    expect(screen.getByText(/Choose a file or drag & drop/)).toBeInTheDocument();
  });

  it("renders the active dataset's header info and grid preview rows", () => {
    listHook.mockReturnValue({ data: { items: [dataset] }, isLoading: false });
    dataHook.mockReturnValue({ data: previewData });

    renderTab();

    expect(screen.getByText("QA Reference Set")).toBeInTheDocument();
    expect(screen.getAllByText("42 rows").length).toBeGreaterThan(0);
    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(screen.getByTestId("gt-grid")).toHaveTextContent("What is 2+2?");
    expect(screen.getByTestId("gt-grid")).toHaveTextContent("Arithmetic");
  });

  it("toggles the 'Use ground truth' switch", async () => {
    listHook.mockReturnValue({ data: { items: [dataset] }, isLoading: false });
    dataHook.mockReturnValue({ data: previewData });

    renderTab();

    expect(
      screen.getByText(/Off\. The evaluator runs without ground truth context\./),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("checkbox"));

    expect(
      screen.getByText(/On\. Retrieved examples are injected/),
    ).toBeInTheDocument();
  });

  it("saves setup changes when an output column is picked and Save is clicked", async () => {
    listHook.mockReturnValue({ data: { items: [dataset] }, isLoading: false });
    dataHook.mockReturnValue({ data: previewData });
    const mutateAsync = vi.fn().mockResolvedValue({});
    saveHook.mockReturnValue({ mutateAsync, isPending: false, isError: false });

    renderTab();

    const [outputSelect] = screen.getAllByRole("combobox");
    await userEvent.click(outputSelect);
    await userEvent.click(await screen.findByRole("option", { name: "answer" }));

    await userEvent.click(screen.getByText("Save"));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    expect(mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        gtId: "gt-1",
        roleMapping: { output: "answer" },
        maxExamples: 3,
        enabled: false,
      }),
    );
  });

  it("deletes the active dataset when the delete icon is clicked", async () => {
    listHook.mockReturnValue({ data: { items: [dataset] }, isLoading: false });
    dataHook.mockReturnValue({ data: previewData });
    const mutateAsync = vi.fn().mockResolvedValue({});
    deleteHook.mockReturnValue({ mutateAsync, isPending: false });

    renderTab();

    const deleteButton = screen
      .getByLabelText("Delete dataset")
      .querySelector("button");
    await userEvent.click(deleteButton);

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledWith("gt-1"));
  });

  it("invokes onSwitchToDetails when 'Test eval' is clicked", async () => {
    listHook.mockReturnValue({ data: { items: [dataset] }, isLoading: false });
    dataHook.mockReturnValue({ data: previewData });
    const onSwitchToDetails = vi.fn();

    renderTab({ onSwitchToDetails });

    await userEvent.click(screen.getByRole("button", { name: /Test eval/i }));

    expect(onSwitchToDetails).toHaveBeenCalled();
  });
});

describe("EvalGroundTruthTab upload wizard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    evalDetailHook.mockReturnValue({
      data: {
        id: "tmpl-1",
        config: { required_keys: [], rule_prompt: "", output_type_normalized: "pass_fail" },
      },
    });
    dataHook.mockReturnValue({ data: undefined });
    statusHook.mockReturnValue({ data: undefined });
    deleteHook.mockReturnValue({ mutateAsync: vi.fn(), isPending: false });
    saveHook.mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({}),
      isPending: false,
      isError: false,
    });
    embedHook.mockReturnValue({ mutateAsync: vi.fn(), isPending: false });
    datasetListHook.mockReturnValue({ data: [], isLoading: false });
    datasetColumnsHook.mockReturnValue({ data: undefined });
    listHook.mockReturnValue({ data: { items: [] }, isLoading: false });
  });

  async function openDrawer() {
    const utils = renderTab();
    await userEvent.click(screen.getByText("Add ground truth dataset"));
    await screen.findByText("Add Ground Truth");
    return utils;
  }

  // jsdom's File/Blob implementation doesn't provide `.text()`, which
  // handleFileUpload relies on to read the dropped file. Patch it onto the
  // File instances created here so the upload flow can be exercised.
  function makeFile(content, name, type) {
    const file = new File([content], name, { type });
    Object.defineProperty(file, "text", {
      value: () => Promise.resolve(content),
    });
    return file;
  }

  it("parses a dropped CSV, previews its columns, and uploads the parsed rows", async () => {
    const mutateAsync = vi.fn().mockResolvedValue({});
    uploadHook.mockReturnValue({ mutateAsync, isPending: false });

    await openDrawer();

    const file = makeFile(
      "question,answer\nWhat is 2+2?,4",
      "qa.csv",
      "text/csv",
    );
    const input = document.querySelector('input[type="file"]');
    await userEvent.upload(input, file);

    expect(await screen.findByText("qa.csv")).toBeInTheDocument();
    expect(screen.getByDisplayValue("qa")).toBeInTheDocument();
    expect(await screen.findByText("Detected columns (2)")).toBeInTheDocument();

    await userEvent.click(screen.getByText("Upload"));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    expect(mutateAsync).toHaveBeenCalledWith({
      name: "qa",
      file_name: "qa.csv",
      columns: ["question", "answer"],
      data: [{ question: "What is 2+2?", answer: "4" }],
    });
    await waitFor(() =>
      expect(enqueueSnackbarMock).toHaveBeenCalledWith(
        "Dataset uploaded successfully",
        { variant: "success" },
      ),
    );
    await waitFor(() =>
      expect(screen.queryByText("Add Ground Truth")).not.toBeInTheDocument(),
    );
  });

  it("parses a dropped JSON file and uploads its records", async () => {
    const mutateAsync = vi.fn().mockResolvedValue({});
    uploadHook.mockReturnValue({ mutateAsync, isPending: false });

    await openDrawer();

    const file = makeFile(
      JSON.stringify([{ question: "Q1", answer: "A1" }]),
      "data.json",
      "application/json",
    );
    const input = document.querySelector('input[type="file"]');
    await userEvent.upload(input, file);

    expect(await screen.findByText("data.json")).toBeInTheDocument();
    expect(await screen.findByText("Detected columns (2)")).toBeInTheDocument();

    await userEvent.click(screen.getByText("Upload"));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    expect(mutateAsync).toHaveBeenCalledWith({
      name: "data",
      file_name: "data.json",
      columns: ["question", "answer"],
      data: [{ question: "Q1", answer: "A1" }],
    });
  });

  it("maps eval template variables to detected file columns and includes the mapping in the upload payload", async () => {
    evalDetailHook.mockReturnValue({
      data: {
        id: "tmpl-1",
        config: { required_keys: ["question"], rule_prompt: "{{question}}" },
      },
    });
    const mutateAsync = vi.fn().mockResolvedValue({});
    uploadHook.mockReturnValue({ mutateAsync, isPending: false });

    await openDrawer();

    const file = makeFile(
      "question,answer\nWhat is 2+2?,4",
      "qa.csv",
      "text/csv",
    );
    const input = document.querySelector('input[type="file"]');
    await userEvent.upload(input, file);

    await screen.findByText(
      "Map eval template variables to columns in your dataset",
    );
    expect(screen.getAllByText("question").length).toBeGreaterThan(0);

    await userEvent.click(screen.getByRole("combobox"));
    await userEvent.click(await screen.findByRole("option", { name: "question" }));

    await userEvent.click(screen.getByText("Upload"));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    expect(mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ variable_mapping: { question: "question" } }),
    );
  });

  it("shows an error snackbar and keeps the drawer open when the file upload fails", async () => {
    const mutateAsync = vi
      .fn()
      .mockRejectedValue({ response: { data: { message: "Bad file" } } });
    uploadHook.mockReturnValue({ mutateAsync, isPending: false });

    await openDrawer();

    const file = makeFile("a,b\n1,2", "f.csv", "text/csv");
    const input = document.querySelector('input[type="file"]');
    await userEvent.upload(input, file);

    await screen.findByText("f.csv");
    await userEvent.click(screen.getByText("Upload"));

    await waitFor(() =>
      expect(enqueueSnackbarMock).toHaveBeenCalledWith("Bad file", {
        variant: "error",
      }),
    );
    expect(screen.getByText("Configure Dataset")).toBeInTheDocument();
  });

  it("imports rows from an existing dataset end-to-end", async () => {
    datasetListHook.mockReturnValue({
      data: [{ dataset_id: "ds-1", name: "Orders", row_count: 120 }],
      isLoading: false,
    });
    datasetColumnsHook.mockReturnValue({
      data: [
        { id: "1", name: "question" },
        { id: "2", name: "answer" },
      ],
    });
    // The importer now reads the dataset one exact, bounded page at a time
    // (ground_truth_dataset_pagination.js), so a page without `column_config`
    // or `metadata` is rejected as unreadable rather than imported.
    axiosGetMock.mockResolvedValue({
      data: {
        result: {
          table: [
            {
              row_id: "r1",
              "1": { cell_value: "Q1" },
              "2": { cell_value: "A1" },
            },
          ],
          column_config: [
            { id: "1", name: "question" },
            { id: "2", name: "answer" },
          ],
          metadata: {
            is_exact: true,
            snapshot_bound: true,
            error_messages: [],
            current_page_index: 0,
            page_size: GROUND_TRUTH_DATASET_PAGE_SIZE,
            total_rows: 1,
            total_pages: 1,
            has_more: false,
            next_page_index: null,
            next_cursor: null,
            dataset_name: "Orders",
          },
        },
      },
    });
    const mutateAsync = vi.fn().mockResolvedValue({});
    uploadHook.mockReturnValue({ mutateAsync, isPending: false });

    await openDrawer();

    await userEvent.click(screen.getByText("Choose from existing dataset"));
    expect(await screen.findByText("Choose Dataset")).toBeInTheDocument();
    expect(screen.getByText("Orders")).toBeInTheDocument();
    expect(screen.getByText("120 rows")).toBeInTheDocument();

    await userEvent.click(screen.getByText("Orders"));

    expect(await screen.findByText("Dataset columns (2)")).toBeInTheDocument();

    // Load rows first: the button only offers Import once the paged read has
    // covered every row, which is the guarantee the importer is built on.
    await userEvent.click(await screen.findByText("Load rows"));
    await userEvent.click(await screen.findByText("Import"));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    expect(mutateAsync).toHaveBeenCalledWith({
      name: "Orders",
      file_name: "Orders.json",
      columns: ["question", "answer"],
      data: [{ question: "Q1", answer: "A1" }],
    });
    await waitFor(() =>
      expect(enqueueSnackbarMock).toHaveBeenCalledWith(
        'Imported 1 rows from "Orders"',
        { variant: "success" },
      ),
    );
  });

  it("warns and skips the upload when the selected dataset has no rows", async () => {
    datasetListHook.mockReturnValue({
      data: [{ dataset_id: "ds-1", name: "Orders", row_count: 0 }],
      isLoading: false,
    });
    datasetColumnsHook.mockReturnValue({ data: [{ id: "1", name: "question" }] });
    axiosGetMock.mockResolvedValue({
      data: {
        result: {
          table: [],
          column_config: [{ id: "1", name: "question" }],
          metadata: {
            is_exact: true,
            snapshot_bound: true,
            error_messages: [],
            current_page_index: 0,
            page_size: GROUND_TRUTH_DATASET_PAGE_SIZE,
            total_rows: 0,
            total_pages: 0,
            has_more: false,
            next_page_index: null,
            next_cursor: null,
            dataset_name: "Orders",
          },
        },
      },
    });
    const mutateAsync = vi.fn().mockResolvedValue({});
    uploadHook.mockReturnValue({ mutateAsync, isPending: false });

    await openDrawer();

    await userEvent.click(screen.getByText("Choose from existing dataset"));
    await userEvent.click(await screen.findByText("Orders"));
    // The emptiness is discovered by the paged read itself, so the warning
    // fires on Load rows and Import never becomes available.
    await userEvent.click(await screen.findByText("Load rows"));

    await waitFor(() =>
      expect(enqueueSnackbarMock).toHaveBeenCalledWith("Dataset has no rows", {
        variant: "warning",
      }),
    );
    // The read completes (there is nothing to page through), so the button
    // does flip to Import — but stays disabled on a zero-row dataset.
    expect(screen.getByRole("button", { name: "Import" })).toBeDisabled();
    expect(mutateAsync).not.toHaveBeenCalled();
    expect(screen.getByText("Configure Dataset")).toBeInTheDocument();
  });

  it("shows an empty state in the dataset picker when no datasets exist", async () => {
    datasetListHook.mockReturnValue({ data: [], isLoading: false });

    await openDrawer();

    await userEvent.click(screen.getByText("Choose from existing dataset"));

    expect(await screen.findByText("No datasets found")).toBeInTheDocument();
  });

  it("navigates back from the dataset picker to the source step", async () => {
    datasetListHook.mockReturnValue({
      data: [{ dataset_id: "ds-1", name: "Orders", row_count: 5 }],
      isLoading: false,
    });

    await openDrawer();

    await userEvent.click(screen.getByText("Choose from existing dataset"));
    await screen.findByText("Choose Dataset");

    await userEvent.click(screen.getByRole("button", { name: "Back" }));

    expect(await screen.findByText("Choose a file or drag & drop")).toBeInTheDocument();
    expect(
      screen.getByText("Choose from existing dataset"),
    ).toBeInTheDocument();
  });

  it("clears the selected file and returns to the source step from the configure screen", async () => {
    await openDrawer();

    const file = new File(["a,b\n1,2"], "f.csv", { type: "text/csv" });
    const input = document.querySelector('input[type="file"]');
    await userEvent.upload(input, file);

    await screen.findByText("f.csv");
    const closeIcons = screen
      .getAllByTestId("icon")
      .filter((el) => el.textContent === "mdi:close");
    await userEvent.click(closeIcons[1].closest("button"));

    expect(
      await screen.findByText("Choose a file or drag & drop"),
    ).toBeInTheDocument();
  });
});

describe("shouldTriggerEmbed", () => {
  it("triggers when GT is enabled, mapping is dirty, and an embed handler exists", () => {
    expect(
      shouldTriggerEmbed({
        enabled: true,
        mappingDirty: true,
        embeddingsReady: true,
        hasOnEmbed: true,
      }),
    ).toBe(true);
  });

  it("triggers when GT is enabled and embeddings are not ready yet", () => {
    expect(
      shouldTriggerEmbed({
        enabled: true,
        mappingDirty: false,
        embeddingsReady: false,
        hasOnEmbed: true,
      }),
    ).toBe(true);
  });

  it("does not trigger when GT is disabled", () => {
    expect(
      shouldTriggerEmbed({
        enabled: false,
        mappingDirty: true,
        embeddingsReady: false,
        hasOnEmbed: true,
      }),
    ).toBe(false);
  });

  it("does not trigger when there is no embed handler", () => {
    expect(
      shouldTriggerEmbed({
        enabled: true,
        mappingDirty: true,
        embeddingsReady: false,
        hasOnEmbed: false,
      }),
    ).toBe(false);
  });

  it("does not trigger when mapping is unchanged and embeddings are already ready", () => {
    expect(
      shouldTriggerEmbed({
        enabled: true,
        mappingDirty: false,
        embeddingsReady: true,
        hasOnEmbed: true,
      }),
    ).toBe(false);
  });
});
