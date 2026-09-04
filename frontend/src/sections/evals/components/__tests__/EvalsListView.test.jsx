import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent, waitFor } from "src/utils/test-utils";
import EvalsListView from "../EvalsListView";

const LIST_URL = "/model-hub/eval-templates/list/";
const CHARTS_URL = "/model-hub/eval-templates/list-charts/";
const NAMES_URL = "/model-hub/get-eval-template-names";
const BULK_DELETE_URL = "/model-hub/eval-templates/bulk-delete/";

const axiosPostMock = vi.hoisted(() => vi.fn());
const mockNavigate = vi.hoisted(() => vi.fn());

vi.mock("src/utils/axios", () => ({
  default: { post: (...args) => axiosPostMock(...args) },
  endpoints: {
    develop: {
      eval: {
        listEvalTemplates: "/model-hub/eval-templates/list/",
        listEvalTemplateCharts: "/model-hub/eval-templates/list-charts/",
        bulkDeleteEvalTemplates: "/model-hub/eval-templates/bulk-delete/",
        getEvalNames: "/model-hub/get-eval-template-names",
      },
    },
  },
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Admin" }),
}));

// react-router (NOT react-router-dom) is where EvalsListView imports
// useNavigate from.
vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, useNavigate: () => mockNavigate };
});

vi.mock("src/hooks/use-ai-filter", () => ({
  useAIFilter: () => ({ parseQuery: vi.fn(), loading: false, error: null }),
}));

const ITEMS = [
  {
    id: "eval-1",
    name: "Toxicity Check",
    eval_type: "llm",
    output_type: "pass_fail",
    template_type: "single",
    tags: [],
    created_by_name: "Jane Smith",
    last_updated: new Date().toISOString(),
    current_version: "1",
  },
  {
    id: "eval-2",
    name: "Relevance Score",
    eval_type: "code",
    output_type: "percentage",
    template_type: "single",
    tags: [],
    created_by_name: "System",
    last_updated: new Date().toISOString(),
    current_version: "2",
  },
];

function mockListResponse(items) {
  axiosPostMock.mockImplementation((url, body) => {
    if (url === LIST_URL) {
      const search = body?.search;
      const filtered = search
        ? items.filter((i) =>
            i.name.toLowerCase().includes(String(search).toLowerCase()),
          )
        : items;
      return Promise.resolve({
        data: { result: { items: filtered, total: filtered.length } },
      });
    }
    if (url === CHARTS_URL) {
      return Promise.resolve({ data: { result: { charts: {} } } });
    }
    if (url === NAMES_URL) {
      return Promise.resolve({ data: { result: [] } });
    }
    if (url === BULK_DELETE_URL) {
      return Promise.resolve({ data: { result: {} } });
    }
    return Promise.resolve({ data: { result: {} } });
  });
}

function renderList() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <EvalsListView />
    </QueryClientProvider>,
  );
}

describe("EvalsListView", () => {
  beforeEach(() => {
    axiosPostMock.mockReset();
    mockNavigate.mockReset();
    window.localStorage.clear();
    mockListResponse(ITEMS);
  });

  it("renders rows from the mocked API response", async () => {
    renderList();

    expect(await screen.findByText("Toxicity Check")).toBeInTheDocument();
    expect(screen.getByText("Relevance Score")).toBeInTheDocument();
  });

  it("re-fetches with the typed search value after the debounce", async () => {
    renderList();
    await screen.findByText("Toxicity Check");
    axiosPostMock.mockClear();

    fireEvent.change(screen.getByPlaceholderText("Search"), {
      target: { value: "toxic" },
    });

    await waitFor(
      () =>
        expect(axiosPostMock).toHaveBeenCalledWith(
          LIST_URL,
          expect.objectContaining({ search: "toxic" }),
        ),
      { timeout: 2000 },
    );

    // The filtered response should drop the non-matching row.
    await waitFor(() =>
      expect(screen.queryByText("Relevance Score")).not.toBeInTheDocument(),
    );
    expect(screen.getByText("Toxicity Check")).toBeInTheDocument();
  });

  it("navigates to the eval detail route when a row is clicked", async () => {
    renderList();
    const row = await screen.findByText("Toxicity Check");

    fireEvent.click(row);

    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith("/dashboard/evaluations/eval-1"),
    );
  });

  it("opens the filter panel when the Filter button is clicked", async () => {
    renderList();
    await screen.findByText("Toxicity Check");

    fireEvent.click(screen.getByRole("button", { name: /Filter/i }));

    expect(
      await screen.findByPlaceholderText(
        "e.g. 'show agent evals tagged Red Teaming'",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Add filter")).toBeInTheDocument();
  });
});
