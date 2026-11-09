import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  deleteRequest: vi.fn(),
  enqueueSnackbar: vi.fn(),
  role: "Admin",
  dataset: {
    id: "ground-truth-1",
    name: "Reference answers",
    row_count: 2,
    columns: ["answer"],
    embedding_status: "completed",
    variable_mapping: {},
    role_mapping: { output: "answer" },
  },
  evalData: { id: "template-1", config: { required_keys: [] } },
}));

vi.mock("notistack", () => ({
  useSnackbar: () => ({ enqueueSnackbar: mocks.enqueueSnackbar }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: mocks.role }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgTheme: () => undefined }));
vi.mock("ag-grid-react", () => ({ AgGridReact: () => null }));
vi.mock("react-dropzone", () => ({
  useDropzone: () => ({
    getRootProps: () => ({}),
    getInputProps: () => ({}),
    isDragActive: false,
  }),
}));
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({ data: [], isLoading: false }),
  useGetDatasetColumns: () => ({ data: [] }),
  useGetDatasetDetail: vi.fn(),
}));
vi.mock("../../hooks/useEvalDetail", () => ({
  useEvalDetail: () => ({ data: mocks.evalData }),
}));
// Keep the real delete mutation and shared dialog. Only the transport and
// unrelated data hooks are mocked, so request counts include the actual hook.
vi.mock("src/utils/axios", () => ({
  default: { delete: (...args) => mocks.deleteRequest(...args) },
  endpoints: {
    develop: {
      eval: {
        deleteGroundTruth: (id) => `/model-hub/ground-truth/${id}/`,
      },
    },
  },
}));
vi.mock("../../hooks/useGroundTruth", async (importOriginal) => {
  /** @type {typeof import("../../hooks/useGroundTruth")} */
  const actual = await importOriginal();
  return {
    ...actual,
    useGroundTruthList: () => ({
      data: { items: [mocks.dataset] },
      isLoading: false,
    }),
    useGroundTruthData: () => ({ data: { rows: [], columns: ["answer"] } }),
    useGroundTruthStatus: () => ({ data: undefined }),
    useUploadGroundTruth: () => ({ isPending: false }),
    useSaveGroundTruthSetup: () => ({ isPending: false }),
    useTriggerEmbedding: () => ({ isPending: false }),
  };
});

import EvalGroundTruthTab from "../EvalGroundTruthTab";

let queryClient;

function renderTab() {
  queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  const view = () => (
    <QueryClientProvider client={queryClient}>
      <EvalGroundTruthTab templateId="template-1" />
    </QueryClientProvider>
  );
  return { ...render(view()), view };
}

function deleteButton() {
  // The existing tooltip labels its wrapper, not the nested icon button.
  return within(screen.getByLabelText("Delete dataset")).getByRole("button");
}

async function openConfirmation() {
  fireEvent.click(deleteButton());
  return screen.findByRole("dialog", { name: "Delete dataset" });
}

describe("Ground Truth delete confirmation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.deleteRequest.mockReset().mockResolvedValue({ data: { result: {} } });
    mocks.role = "Admin";
    mocks.dataset = { ...mocks.dataset, id: "ground-truth-1" };
  });

  afterEach(() => {
    cleanup();
    queryClient?.clear();
  });

  it("opens the confirmation without sending a delete request", async () => {
    renderTab();
    fireEvent.click(deleteButton());

    const dialog = await screen.findByRole("dialog", {
      name: "Delete dataset",
    });
    expect(mocks.deleteRequest).not.toHaveBeenCalled();
    expect(within(dialog).getByText("Reference answers")).toBeInTheDocument();
    expect(
      within(dialog).getByText("This action cannot be undone."),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "Cancel" }),
    ).toBeEnabled();
    expect(within(dialog).getByRole("button", { name: "Delete" })).toHaveClass(
      "MuiButton-containedError",
    );
  });

  it("cancels without sending a delete request", async () => {
    renderTab();
    const dialog = await openConfirmation();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(mocks.deleteRequest).not.toHaveBeenCalled();
    expect(deleteButton()).toBeEnabled();
  });

  it("sends exactly one delete request only after confirmation", async () => {
    renderTab();
    const dialog = await openConfirmation();
    expect(mocks.deleteRequest).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(mocks.deleteRequest).toHaveBeenCalledTimes(1));
    expect(mocks.deleteRequest).toHaveBeenCalledWith(
      "/model-hub/ground-truth/ground-truth-1/",
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(mocks.enqueueSnackbar).toHaveBeenCalledWith("Dataset deleted", {
      variant: "success",
    });
  });

  it("prevents duplicate confirmation while the delete request is pending", async () => {
    let resolveDelete;
    mocks.deleteRequest.mockReturnValue(
      new Promise((resolve) => {
        resolveDelete = resolve;
      }),
    );
    renderTab();
    const dialog = await openConfirmation();
    const confirm = within(dialog).getByRole("button", { name: "Delete" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    fireEvent.keyDown(dialog, { key: "Enter" });

    await waitFor(() => expect(mocks.deleteRequest).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(confirm).toBeDisabled());
    expect(
      within(dialog).getByRole("button", { name: "Cancel" }),
    ).toBeDisabled();
    resolveDelete({ data: { result: {} } });
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(mocks.deleteRequest).toHaveBeenCalledTimes(1);
  });

  it("keeps a failed deletion available for an explicit retry", async () => {
    mocks.deleteRequest.mockRejectedValueOnce(new Error("Delete failed"));
    renderTab();
    const dialog = await openConfirmation();
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() =>
      expect(mocks.enqueueSnackbar).toHaveBeenCalledWith("Failed to delete", {
        variant: "error",
      }),
    );
    const confirm = within(dialog).getByRole("button", { name: "Delete" });
    await waitFor(() => expect(confirm).toBeEnabled());
    expect(mocks.deleteRequest).toHaveBeenCalledTimes(1);
    fireEvent.click(confirm);
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(mocks.deleteRequest).toHaveBeenCalledTimes(2);
  });

  it("does not offer deletion to a read-only user", () => {
    mocks.role = "Viewer";
    renderTab();
    expect(deleteButton()).toBeDisabled();
    fireEvent.click(deleteButton());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mocks.deleteRequest).not.toHaveBeenCalled();
  });

  it("dismisses confirmation if the active dataset changes", async () => {
    const { rerender, view } = renderTab();
    await openConfirmation();
    mocks.dataset = { ...mocks.dataset, id: "ground-truth-2" };
    rerender(view());

    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(mocks.deleteRequest).not.toHaveBeenCalled();
  });
});
