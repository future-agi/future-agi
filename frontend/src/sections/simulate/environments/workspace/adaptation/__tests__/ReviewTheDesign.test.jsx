import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  render as baseRender,
  fireEvent,
  screen,
  waitFor,
} from "src/utils/test-utils";

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

vi.mock("src/api/harness/releases", () => ({
  useHarnessAdaptationDesign: vi.fn(),
  approveHarnessAdaptation: vi.fn(),
  harnessAdaptationDesignKey: (id) => ["harness-adaptation-design", id],
}));

vi.mock("src/api/harness/harness", () => ({ cancelHarnessJob: vi.fn() }));

// The chat is the builder's own console; this page only places it.
vi.mock("../../../buildEnvironment/console/BuilderConsole", () => ({
  default: () => <div>Builder chat</div>,
}));

const { useHarnessAdaptationDesign, approveHarnessAdaptation } = await import(
  "src/api/harness/releases"
);
const { cancelHarnessJob } = await import("src/api/harness/harness");
const { default: ReviewTheDesign } = await import("../ReviewTheDesign");

const DESIGN = {
  state: "checking",
  failure: null,
  composition_id: null,
  agent: { name: "ShopMate", version: "v14" },
  world: {
    name: "retail",
    title: "Retail",
    summary: "An online clothing store.",
    channel: "voice",
    release: "sha256:ab",
    records: { orders: 38, customers: 12, drafts: 0 },
    tools: [
      { name: "get_order", description: "" },
      { name: "cancel_order", description: "" },
    ],
    rules: [
      { id: "verify_first", text: "Verify the owner first." },
      { id: "confirm_cancel", text: "Confirm before cancelling an order." },
    ],
    people: ["Gareth Hughes"],
  },
  suite: "retail_base",
  scenarios: [
    { key: "lookup", title: "Look up an order" },
    { key: "cancel", title: "Cancel an order" },
  ],
  fit: {
    agent: { name: "ShopMate", version: "v14" },
    agent_tools: [{ name: "order_details" }],
    rows: [
      { world: "get_order", agent: "order_details", kind: "mapped" },
      { world: "cancel_order", agent: null, kind: "missing" },
    ],
    platform_actions: [],
    total: 2,
    unmeasured: [{ key: "cancel", title: "Cancel an order" }],
  },
  conflicts: {
    checked: true,
    conflicts: [
      {
        rule: "confirm_cancel",
        policy: "Confirm before cancelling an order.",
        agent: "Cancel straight away.",
      },
    ],
  },
  check: null,
};

const CHAT = { turns: [], running: false, send: vi.fn() };

const render = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  baseRender(
    <QueryClientProvider client={client}>
      <ReviewTheDesign jobId="adapt-job" chat={CHAT} />
    </QueryClientProvider>,
  );
};

const approve = () => screen.getByRole("button", { name: "Approve and build" });

beforeEach(() => {
  navigate.mockReset();
  approveHarnessAdaptation.mockReset();
  cancelHarnessJob.mockReset();
  useHarnessAdaptationDesign.mockReset();
  useHarnessAdaptationDesign.mockReturnValue({ data: DESIGN, isError: false });
});

describe("Review the design", () => {
  it("opens at once with the design read from the ready world, awaiting approval", () => {
    render();

    expect(screen.getByText("Retail · ShopMate")).toBeInTheDocument();
    expect(screen.getByText("Awaiting approval")).toBeInTheDocument();
    expect(screen.getByText("Runs: Live")).toBeInTheDocument();
    expect(screen.getByText("Builder chat")).toBeInTheDocument();
    expect(
      screen.getByText(
        "2 records · 2 tools · 2 rules · 2 scenarios — nothing is built until you approve.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("What the design is built from"),
    ).toBeInTheDocument();
    expect(screen.getByText("An online clothing store.")).toBeInTheDocument();
    expect(screen.getByText("The ready world")).toBeInTheDocument();
    expect(screen.getByText("Voice")).toBeInTheDocument();
    expect(screen.getByText("ShopMate · v14")).toBeInTheDocument();
    for (const row of ["Records", "Tools", "Rules", "People", "Scenarios"]) {
      expect(
        screen.getByRole("button", { name: new RegExp(`^${row}`) }),
      ).toHaveAttribute("aria-expanded", "false");
    }
    // Nothing to approve until the mapping has been checked on every case.
    expect(approve()).toBeDisabled();
  });

  it("lists the pairing, the conflicts and the cases it can't measure", () => {
    render();

    fireEvent.click(screen.getByRole("button", { name: /^Tools/ }));
    fireEvent.click(screen.getByRole("button", { name: /^Rules/ }));
    fireEvent.click(screen.getByRole("button", { name: /^Scenarios/ }));

    expect(screen.getByText("order_details")).toBeInTheDocument();
    expect(screen.getByText("Not available")).toBeInTheDocument();
    expect(
      screen.getByText("ShopMate's instructions say: “Cancel straight away.”"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/1 of 2 measurable with ShopMate\./),
    ).toBeInTheDocument();
    expect(screen.getByText("Not measurable")).toBeInTheDocument();
  });

  it("approves and builds once every case has been checked", async () => {
    useHarnessAdaptationDesign.mockReturnValue({
      data: {
        ...DESIGN,
        state: "awaiting_approval",
        check: {
          mapped: [{ agent_tool: "order_details", steps: ["get_order"] }],
          unscorable: [],
          total: 2,
          scorable: 2,
        },
      },
      isError: false,
    });
    approveHarnessAdaptation.mockResolvedValue({ id: "adapt-job" });
    render();

    fireEvent.click(approve());

    await waitFor(() =>
      expect(approveHarnessAdaptation).toHaveBeenCalledWith("adapt-job"),
    );
    fireEvent.click(screen.getByRole("button", { name: /^Scenarios/ }));
    // The check's findings replace the pairing's estimate.
    expect(
      screen.getByText(/2 of 2 measurable with ShopMate\./),
    ).toBeInTheDocument();
  });

  it("cancels the adaptation and goes back to the environments", async () => {
    cancelHarnessJob.mockResolvedValue({});
    render();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(navigate).toHaveBeenCalled());
    expect(cancelHarnessJob.mock.calls[0][0]).toBe("adapt-job");
  });

  it("says when the design can't be loaded", () => {
    useHarnessAdaptationDesign.mockReturnValue({
      data: undefined,
      isError: true,
      error: new Error("adaptation not found"),
      refetch: vi.fn(),
    });
    render();

    expect(screen.getByText("Couldn't load the design")).toBeInTheDocument();
  });
});
