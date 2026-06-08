/* eslint-disable react/prop-types */
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  within,
  fireEvent,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import FolderListView from "../FolderListView";
import { usePromptStore } from "../../store/usePromptStore";
import axios from "src/utils/axios";

// ---- Mock react-router ----
vi.mock("react-router", () => ({
  useParams: () => ({ folder: "all" }),
}));

// ---- Mock axios ----
let mockPromptsResponse = null;
vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(() => Promise.resolve(mockPromptsResponse)),
  },
  endpoints: {
    develop: {
      runPrompt: {
        promptExecutions: () => "/model-hub/prompt-executions/",
        promptTemplate: "/model-hub/prompt-base-templates/",
      },
    },
  },
}));

// ---- Mock auth ----
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "USER" }),
}));

vi.mock("src/utils/rolePermissionMapping", () => ({
  PERMISSIONS: { CREATE: "CREATE" },
  RolePermission: { PROMPTS: { CREATE: { USER: true } } },
}));

// ---- Mock child components ----
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

vi.mock("src/components/EmptyLayout/EmptyLayout", () => ({
  default: ({ title, description, action }) => (
    <div data-testid="empty-layout">
      <h1>{title}</h1>
      <p>{description}</p>
      {action}
    </div>
  ),
}));

// ---- Helpers ----
const buildResponse = ({ pageSize, totalCount }) => {
  const totalPages = Math.max(1, Math.ceil(totalCount / pageSize));
  return {
    data: {
      results: Array.from(
        { length: Math.min(pageSize, totalCount) },
        (_, i) => ({ id: `p-${i}`, name: `Prompt ${i}`, type: "PROMPT" }),
      ),
      count: totalCount,
      total_pages: totalPages,
    },
  };
};

function renderView(route = "/dashboard/workbench/all") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]}>
        <FolderListView
          sortConfig={{ field: "updated_at", direction: "desc" }}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---- Tests ----
describe("FolderListView — pagination (TH-4245)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    usePromptStore.setState({ searchQuery: "", newPromptModal: false });
    mockPromptsResponse = buildResponse({ pageSize: 10, totalCount: 43 });
    axios.get.mockImplementation(() => Promise.resolve(mockPromptsResponse));
  });

  it("renders pagination count derived from API total_pages", async () => {
    renderView();

    const pagination = await screen.findByRole("navigation");
    expect(
      within(pagination).getByRole("button", { name: /go to page 5/i }),
    ).toBeInTheDocument();
    expect(
      within(pagination).queryByRole("button", { name: /go to page 6/i }),
    ).not.toBeInTheDocument();
  });

  it("recalculates pagination count when pageLimit changes (no ghost pages)", async () => {
    const axios = (await import("src/utils/axios")).default;
    axios.get.mockImplementation((_url, { params }) =>
      Promise.resolve(
        buildResponse({ pageSize: params.page_size, totalCount: 43 }),
      ),
    );

    renderView();

    const pagination = await screen.findByRole("navigation");
    await waitFor(() =>
      expect(
        within(pagination).getByRole("button", { name: /go to page 5/i }),
      ).toBeInTheDocument(),
    );

    // Change "Result per page" from 10 -> 50
    fireEvent.mouseDown(screen.getByRole("combobox"));
    fireEvent.click(await screen.findByRole("option", { name: "50" }));

    await waitFor(() =>
      expect(
        within(pagination).queryByRole("button", { name: /go to page 5/i }),
      ).not.toBeInTheDocument(),
    );
    expect(
      within(pagination).queryByRole("button", { name: /go to page 2/i }),
    ).not.toBeInTheDocument();
    expect(
      within(pagination).getByRole("button", { name: /page 1/i }),
    ).toBeInTheDocument();
  });

  it("shows prompt-loop copy for normal empty prompt folders", async () => {
    mockPromptsResponse = buildResponse({ pageSize: 10, totalCount: 0 });

    renderView();

    expect(await screen.findByTestId("empty-layout")).toBeInTheDocument();
    expect(screen.getByText("Create your first prompt")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Write or generate a prompt, run it with a focused input, and save versions as you improve the output.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Manage datasets/i)).not.toBeInTheDocument();
  });

  it("shows guided prompt-loop copy on onboarding empty routes", async () => {
    mockPromptsResponse = buildResponse({ pageSize: 10, totalCount: 0 });

    renderView(
      "/dashboard/workbench/all?source=onboarding&action=create-prompt",
    );

    expect(await screen.findByTestId("empty-layout")).toBeInTheDocument();
    expect(
      screen.getByText("Start your first prompt loop"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Create one prompt, run it against an example, save the baseline, then compare the next version.",
      ),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Create prompt" }));
    expect(usePromptStore.getState().newPromptModal).toBe(true);
  });
});
