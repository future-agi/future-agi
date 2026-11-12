/* eslint-disable react/prop-types */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import {
  QueryClient,
  QueryClientProvider,
  onlineManager,
} from "@tanstack/react-query";
import axios from "src/utils/axios";
import FolderListView from "../FolderListView";
import { usePromptStore } from "../../store/usePromptStore";

// W10-DESIGN-SYSTEM-U01: a prompt list that failed to load (or is waiting for
// the network) must not look like a workspace with no prompts.

let mockFolder = "all";
vi.mock("react-router", () => ({
  useParams: () => ({ folder: mockFolder }),
}));

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: {
    develop: {
      runPrompt: {
        promptExecutions: () => "/model-hub/prompt-executions/",
        promptTemplate: "/model-hub/prompt-base-templates/",
      },
    },
  },
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "USER" }),
}));

vi.mock("src/utils/rolePermissionMapping", () => ({
  PERMISSIONS: { CREATE: "CREATE" },
  RolePermission: { PROMPTS: { CREATE: { USER: true } } },
}));

vi.mock("../PromptItem", () => ({
  default: ({ name, isLoading }) =>
    isLoading ? (
      <div data-testid="prompt-item-loading" />
    ) : (
      <div data-testid="prompt-item">{name}</div>
    ),
}));

vi.mock("../../../workbench/SelectedPromptTemplateDrawer", () => ({
  SelectedPromptTemplateDrawer: () => null,
}));

// The first-run screen renders its title and action so the test can see them.
vi.mock("src/components/EmptyLayout/EmptyLayout", () => ({
  default: ({ title, action }) => (
    <div data-testid="empty-layout">
      <span>{title}</span>
      {action}
    </div>
  ),
}));

// The shape src/utils/axios.js rejects with when the request never got a
// response (offline, connection reset): no `result`, so no global toast.
const NETWORK_ERROR = {
  message: "Network Error",
  statusCode: undefined,
  transportCode: "ERR_NETWORK",
  responseHeaders: { "x-request-id": undefined },
};

const promptsResponse = {
  data: {
    results: [{ id: "p-0", name: "Prompt 0", type: "PROMPT" }],
    count: 1,
    total_pages: 1,
  },
};

const templatesResponse = {
  data: {
    result: {
      data: [{ id: "t-0", name: "Template 0" }],
      total_count: 1,
      total_pages: 1,
    },
  },
};

function renderView() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <FolderListView sortConfig={{ field: "updated_at", direction: "desc" }} />
    </QueryClientProvider>,
  );
}

async function waitForLoadToSettle() {
  await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
  await waitFor(() =>
    expect(screen.queryByTestId("prompt-item-loading")).not.toBeInTheDocument(),
  );
}

describe("FolderListView — load errors (W10-DESIGN-SYSTEM-U01)", () => {
  beforeEach(() => {
    mockFolder = "all";
    axios.get.mockReset();
    usePromptStore.setState({ searchQuery: "", newPromptModal: false });
  });

  afterEach(() => {
    onlineManager.setOnline(true);
  });

  it("shows an error with Retry, not 'No prompts yet', when the prompt list fails to load", async () => {
    axios.get
      .mockRejectedValueOnce(NETWORK_ERROR)
      .mockResolvedValue(promptsResponse);

    renderView();
    await waitForLoadToSettle();

    expect(screen.queryByText("No prompts yet")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Create prompt" }),
    ).not.toBeInTheDocument();

    const alert = await screen.findByRole("alert");
    expect(
      within(alert).getByText("Couldn't load your prompts"),
    ).toBeInTheDocument();
    expect(
      within(alert).getByText("Check your connection and try again."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Network Error")).not.toBeInTheDocument();

    const retry = within(alert).getByRole("button", { name: "Retry" });
    expect(retry).toBeEnabled();
    fireEvent.click(retry);

    expect(await screen.findByText("Prompt 0")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(axios.get).toHaveBeenCalledTimes(2);
  });

  it("shows an error with Retry, not 'No templates yet', when templates fail to load", async () => {
    mockFolder = "my-templates";
    axios.get
      .mockRejectedValueOnce(NETWORK_ERROR)
      .mockResolvedValue(templatesResponse);

    renderView();
    await waitForLoadToSettle();

    expect(screen.queryByText("No templates yet")).not.toBeInTheDocument();

    const alert = await screen.findByRole("alert");
    expect(
      within(alert).getByText("Couldn't load your templates"),
    ).toBeInTheDocument();

    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("Template 0")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows the error while offline and loads the prompts once back online", async () => {
    axios.get.mockResolvedValue(promptsResponse);
    onlineManager.setOnline(false);

    renderView();

    const alert = await screen.findByRole("alert");
    expect(
      within(alert).getByText("Couldn't load your prompts"),
    ).toBeInTheDocument();
    expect(screen.queryByText("No prompts yet")).not.toBeInTheDocument();
    expect(axios.get).not.toHaveBeenCalled();

    await act(async () => {
      onlineManager.setOnline(true);
    });

    expect(await screen.findByText("Prompt 0")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
