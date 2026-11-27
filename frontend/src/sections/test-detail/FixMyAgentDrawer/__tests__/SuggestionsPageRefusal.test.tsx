import { fireEvent, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "src/utils/test-utils";

// The drawer's reads and writes of the optimiser analysis; the rest is real.
const server = vi.hoisted(() => ({
  get: vi.fn<(url: string) => Promise<unknown>>(),
  post: vi.fn<(url: string) => Promise<unknown>>(),
}));

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  default: {
    get: (url: string) => server.get(url),
    post: (url: string) => server.post(url),
  },
}));
vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useParams: () => ({ executionId: "e1" }),
}));
vi.mock("../../context/TestDetailContext", () => ({
  useTestDetail: () => ({ getGridApi: () => null }),
}));

const { default: SuggestionsPage } = await import("../SuggestionsPage");

const NOTHING_FAILED =
  "Nothing failed in this run, so there’s nothing to debug.";

// What the axios interceptor rejects with for the server's 409 refusal.
const refused = (code: string) =>
  Promise.reject({ statusCode: 409, code, detail: "server words" });

// The run page's KPI read, which the button's counts come from.
const KPIS_KEY = ["test-execution-detail", "KPIS", "e1"];

const renderDrawer = () => {
  // The app's own default: a failed read is retried once.
  const client = new QueryClient({
    defaultOptions: { queries: { retry: 1, retryDelay: 0 } },
  });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  render(
    <QueryClientProvider client={client}>
      <SuggestionsPage onClose={() => {}} />
    </QueryClientProvider>,
  );
  return { invalidate };
};

describe("Debug failures drawer on a run the rule refuses", () => {
  beforeEach(() => {
    server.get.mockReset();
    server.post.mockReset();
  });

  it("shows the refusal's reason, offers no retry, and reads once", async () => {
    server.get.mockImplementation(() =>
      refused("investigation_nothing_failed"),
    );

    const { invalidate } = renderDrawer();

    expect(await screen.findByText(NOTHING_FAILED)).toBeInTheDocument();
    expect(screen.queryByText(/click the refresh button/)).toBeNull();
    expect(screen.queryByRole("button", { name: /refresh/i })).toBeNull();
    expect(server.get).toHaveBeenCalledTimes(1);
    // The page's counts let the click through, so they were stale: read them again.
    expect(invalidate).toHaveBeenCalledWith({ queryKey: KPIS_KEY });
  });

  it.each([
    [
      "execution_not_completed",
      "Diagnosis is available once every call in this run finishes.",
    ],
    [
      "execution_calls_unfinished",
      "Some calls in this run didn’t finish, so Debug failures can’t read it.",
    ],
  ])("says why a %s refusal started no analysis", async (code, words) => {
    server.get.mockImplementation(() => refused(code));

    renderDrawer();

    expect(await screen.findByText(words)).toBeInTheDocument();
    expect(screen.queryByText("server words")).toBeNull();
    expect(server.get).toHaveBeenCalledTimes(1);
  });

  it("keeps the run's analysis when a Refresh is refused", async () => {
    server.get.mockResolvedValue({
      data: {
        result: {
          status: "completed",
          last_updated: "2026-11-27T10:00:00Z",
          response: {
            insights: "Earlier analysis",
            agent_level: { actionable_recommendations: [] },
            system_level: { actionable_recommendations: [] },
          },
        },
      },
    });
    server.post.mockImplementation(() =>
      refused("investigation_nothing_failed"),
    );

    const { invalidate } = renderDrawer();
    fireEvent.click(await screen.findByRole("button", { name: "Refresh" }));

    expect(await screen.findByText(NOTHING_FAILED)).toBeInTheDocument();
    expect(screen.getByText(/Last updated at/)).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Refresh" })).toBeNull(),
    );
    expect(server.post).toHaveBeenCalledTimes(1);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: KPIS_KEY });
  });
});
