import { describe, it, expect, vi, beforeEach, type Mock } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// The drawer runs against the real `useDebugAnalysis`; only the transport and
// the neighbours it does not own are stubbed.
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return { ...actual, default: { get: vi.fn(), post: vi.fn() } };
});
vi.mock("src/api/simulate-environments/runDetail", async (importOriginal) => ({
  ...(await importOriginal()),
  useRunCalls: () => ({ tasks: [], groups: [], isLoading: false }),
}));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useFeatureAllowed: () => ({
    allowed: false,
    isLoading: false,
    isError: false,
  }),
}));

// The mocked transport: only `get` and `post` exist on it.
const { default: transport } = await import("src/utils/axios");
const axios = transport as unknown as { get: Mock; post: Mock };
const { default: FixMyAgentDrawer } = await import("../FixMyAgentDrawer");

const TOO_FEW = "You need at least 15 connected calls to debug failures.";

const renderDrawer = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const tree = (open: boolean) => (
    <QueryClientProvider client={client}>
      <FixMyAgentDrawer
        open={open}
        onClose={() => {}}
        executionId="ex1"
        stats={{ failed: 1 }}
        blockedReason={null}
        onLaunch={() => {}}
        onViewCalls={() => {}}
      />
    </QueryClientProvider>
  );
  const view = render(tree(true));
  return { ...view, setOpen: (open: boolean) => view.rerender(tree(open)) };
};

beforeEach(() => {
  axios.get.mockReset();
  axios.post.mockReset();
});

describe("FixMyAgentDrawer request errors", () => {
  it("says so when a retry of a failed diagnosis fails too", async () => {
    axios.get.mockResolvedValue({
      data: { status: "failed", error_message: "Debug analysis failed" },
    });
    axios.post.mockRejectedValue({
      statusCode: 500,
      message: "Internal Server Error",
    });
    const user = userEvent.setup();
    renderDrawer();

    expect(await screen.findByText(/Debug analysis failed/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Try again" }));

    // The old failure is gone: the retry's own failure is what the page says.
    expect(
      await screen.findByText("Couldn't read this run. Try again."),
    ).toBeVisible();
    expect(screen.queryByText(/Debug analysis failed/)).toBeNull();
    expect(screen.getByRole("button", { name: "Try again" })).toBeVisible();
    expect(axios.post).toHaveBeenCalledTimes(1);
  });

  it("starts fresh on a reopen: no old refusal, and the request is sent again", async () => {
    axios.get.mockResolvedValue({ data: { status: "not_requested" } });
    axios.post.mockRejectedValueOnce({ code: "investigation_too_few_calls" });
    axios.post.mockResolvedValue({ data: { status: "running" } });
    const { setOpen } = renderDrawer();

    expect(await screen.findByText(TOO_FEW)).toBeVisible();
    expect(axios.post).toHaveBeenCalledTimes(1);

    // The run has since become eligible; the drawer stays mounted throughout.
    setOpen(false);
    setOpen(true);

    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByText(TOO_FEW)).toBeNull());
  });
});
