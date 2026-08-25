/* eslint-disable react/prop-types */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor } from "../../utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import GlobalVariableDrawer from "src/sections/agent-playground/AgentBuilder/GlobalVariablesPanel/GlobalVariableDrawer";
import { useGlobalVariablesDrawerStore } from "src/sections/agent-playground/store";

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useParams: () => ({ agentId: "agent-1" }),
    useSearchParams: () => [new URLSearchParams("version=ver-1")],
  };
});

let mockDatasetData = null;
let mockIsLoading = false;
vi.mock("src/api/agent-playground/agent-playground", () => ({
  useGetGraphDataset: () => ({
    data: mockDatasetData,
    isLoading: mockIsLoading,
  }),
  useUpdateDatasetCell: () => ({ mutateAsync: vi.fn() }),
}));

vi.mock(
  "src/sections/agent-playground/AgentBuilder/GlobalVariablesPanel/ManualVariablesForm",
  () => ({
    default: ({ formValues }) => (
      <div data-testid="manual-form">{JSON.stringify(formValues)}</div>
    ),
  }),
);

vi.mock(
  "src/sections/agent-playground/AgentBuilder/GlobalVariablesPanel/UploadedJSON",
  () => ({
    default: ({ uploadedJson }) => (
      <div data-testid="uploaded-json">{JSON.stringify(uploadedJson)}</div>
    ),
  }),
);

vi.mock(
  "src/sections/agent-playground/AgentBuilder/GlobalVariablesPanel/HeaderActions",
  () => ({
    default: () => <div data-testid="header-actions" />,
  }),
);

vi.mock("src/components/svg-color", () => ({
  default: (props) => <span data-testid="svg-icon" {...props} />,
}));

vi.mock("src/components/upload-json-dialog", () => ({
  UploadJsonDialog: ({ open }) =>
    open ? <div data-testid="upload-dialog" /> : null,
}));

vi.mock("src/components/custom-dialog/confirm-dialog", () => ({
  default: ({ open, action, onClose }) =>
    open ? (
      <div data-testid="confirm-dialog">
        {action}
        <button data-testid="cancel-close" onClick={onClose}>
          Cancel
        </button>
      </div>
    ) : null,
}));

function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false, gcTime: 0 },
    },
  });
}

function renderDrawer(props = {}) {
  const defaultProps = { open: true, onClose: vi.fn(), ...props };
  const queryClient = createTestQueryClient();

  return render(
    <QueryClientProvider client={queryClient}>
      <GlobalVariableDrawer {...defaultProps} />
    </QueryClientProvider>,
  );
}

describe("GlobalVariableDrawer dataset integration", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useGlobalVariablesDrawerStore.getState().reset();
    mockDatasetData = null;
    mockIsLoading = false;
  });

  it("syncs dataset variables to store on load", async () => {
    mockDatasetData = {
      columns: [
        { id: "col-1", name: "city" },
        { id: "col-2", name: "country" },
      ],
      rows: [
        {
          cells: [
            { columnId: "col-1", value: "Tokyo" },
            { columnId: "col-2", value: "Japan" },
          ],
        },
      ],
    };

    renderDrawer();

    await waitFor(() => {
      const state = useGlobalVariablesDrawerStore.getState();
      expect(state.globalVariables).toEqual({
        city: "Tokyo",
        country: "Japan",
      });
    });
  });

  it("handles missing cells gracefully with an empty string fallback", async () => {
    mockDatasetData = {
      columns: [
        { id: "col-1", name: "city" },
        { id: "col-2", name: "country" },
      ],
      rows: [
        {
          cells: [{ columnId: "col-1", value: "Tokyo" }],
        },
      ],
    };

    renderDrawer();

    await waitFor(() => {
      const state = useGlobalVariablesDrawerStore.getState();
      expect(state.globalVariables.country).toBe("");
    });
  });

  it("does not sync when dataset data is missing", () => {
    mockDatasetData = null;
    renderDrawer();

    const state = useGlobalVariablesDrawerStore.getState();
    expect(state.globalVariables).toEqual({});
  });
});
