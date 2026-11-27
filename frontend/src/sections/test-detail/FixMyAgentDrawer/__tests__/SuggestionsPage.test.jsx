import { describe, it, expect, vi, beforeEach } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockGet = vi.fn();
const mockPost = vi.fn();

// Keep the real endpoints so the URLs are the genuine ones.
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: {
      get: (...args) => mockGet(...args),
      post: (...args) => mockPost(...args),
    },
  };
});

vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ executionId: "ex-1" }),
}));

const { endpoints } = await import("src/utils/axios");
const { default: SuggestionsPage } = await import("../SuggestionsPage");

const NEVER_ANALYSED = {
  data: {
    result: {
      response: null,
      status: "completed",
      message: "No optimiser runs found. Trigger a refresh to start analysis.",
    },
  },
};

const renderPage = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SuggestionsPage onClose={() => {}} />
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  mockGet.mockResolvedValue(NEVER_ANALYSED);
  mockPost.mockResolvedValue({ data: { result: { status: "pending" } } });
});

describe("Fix My Agent on a run nobody has analysed", () => {
  it("offers a Refresh that starts the analysis once, and opening starts nothing", async () => {
    renderPage();

    const refresh = await screen.findByRole("button", { name: /refresh/i });
    // The wording the E2E flow SIM-E2E-002 reads in this drawer.
    expect(
      screen.getByText(
        "There are no suggestions yet, click the refresh button to get suggestions",
        { exact: true },
      ),
    ).toBeInTheDocument();
    expect(mockGet).toHaveBeenCalledWith(
      endpoints.testExecutions.getOptimizerAnalysis("ex-1"),
    );
    expect(mockPost).not.toHaveBeenCalled();

    await userEvent.click(refresh);

    expect(mockPost).toHaveBeenCalledTimes(1);
    expect(mockPost).toHaveBeenCalledWith(
      endpoints.testExecutions.refreshOptimizerAnalysis("ex-1"),
    );
  });
});
