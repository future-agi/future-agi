import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  enqueueSnackbar: vi.fn(),
  role: "Admin",
  items: [],
  dropzone: null,
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
  useDropzone: (options) => {
    // Keep the drop handler so a test can choose a file without a real drop.
    mocks.dropzone = options;
    return {
      getRootProps: () => ({}),
      getInputProps: () => ({}),
      isDragActive: false,
    };
  },
}));
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({
    data: [{ id: "dataset-1", name: "Reference data", row_count: 101 }],
    isLoading: false,
  }),
  useGetDatasetColumns: () => ({ data: [] }),
  useGetDatasetDetail: vi.fn(),
}));
vi.mock("../../hooks/useEvalDetail", () => ({
  useEvalDetail: () => ({ data: mocks.evalData }),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
}));
vi.mock("../../hooks/useGroundTruth", () => ({
  useGroundTruthList: () => ({
    data: { items: mocks.items },
    isLoading: false,
  }),
  useGroundTruthData: () => ({ data: { rows: [], columns: ["answer"] } }),
  useGroundTruthStatus: () => ({ data: undefined }),
  useUploadGroundTruth: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useSaveGroundTruthSetup: () => ({ isPending: false }),
  useTriggerEmbedding: () => ({ isPending: false }),
  useDeleteGroundTruth: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

import EvalGroundTruthTab, { UploadDrawer } from "../EvalGroundTruthTab";

function renderWithClient(ui) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

const renderTab = () =>
  renderWithClient(<EvalGroundTruthTab templateId="template-1" />);

const renderDrawer = (onClose = vi.fn()) =>
  renderWithClient(
    <UploadDrawer
      open
      onClose={onClose}
      templateId="template-1"
      evalVariables={[]}
    />,
  );

describe("Ground Truth keyboard access (W10-EVALS-U02)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.role = "Admin";
    mocks.items = [];
    mocks.dropzone = null;
  });

  it("opens the upload drawer from the empty state with the keyboard", async () => {
    const user = userEvent.setup();
    renderTab();

    const add = screen.getByRole("button", { name: "Add ground truth" });
    expect(add).toBeEnabled();
    act(() => add.focus());
    expect(add).toHaveFocus();
    expect(
      screen.getByText("Upload a file or choose a dataset"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Click anywhere to upload"),
    ).not.toBeInTheDocument();

    await user.keyboard("{Enter}");
    expect(
      await screen.findByText("Choose a file or drag & drop"),
    ).toBeInTheDocument();
  });

  it("shows the empty-state button as unavailable to read-only users", () => {
    mocks.role = "Viewer";
    renderTab();

    expect(
      screen.getByRole("button", { name: "Add ground truth" }),
    ).toBeDisabled();
  });

  it("names the upload and delete buttons in the dataset header", async () => {
    mocks.items = [mocks.dataset];
    renderTab();

    const upload = screen.getByRole("button", { name: "Upload new dataset" });
    expect(upload).toBeEnabled();
    expect(
      screen.getByRole("button", { name: "Delete dataset" }),
    ).toBeEnabled();

    fireEvent.click(upload);
    expect(
      await screen.findByText("Choose a file or drag & drop"),
    ).toBeInTheDocument();
  });

  it("lets keyboard users choose an existing dataset in the drawer", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderDrawer(onClose);

    const existing = screen.getByRole("button", {
      name: /Choose from existing dataset/,
    });
    act(() => existing.focus());
    expect(existing).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(screen.getByText("Choose Dataset")).toBeInTheDocument();

    const row = screen.getByRole("button", { name: /Reference data/ });
    act(() => row.focus());
    expect(row).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(screen.getByText("Configure Dataset")).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "Choose a different dataset" }),
    );
    expect(screen.getByText("Choose Dataset")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("names the button that removes a chosen file", () => {
    renderDrawer();

    // An .xlsx file skips the client-side column preview, so this is synchronous.
    act(() => {
      mocks.dropzone.onDrop([new File(["answer"], "answers.xlsx")]);
    });
    expect(screen.getByText("answers.xlsx")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Remove file" }));
    expect(screen.queryByText("answers.xlsx")).not.toBeInTheDocument();
    expect(
      screen.getByText("Choose a file or drag & drop"),
    ).toBeInTheDocument();
  });
});
