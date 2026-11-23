import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  render as baseRender,
  fireEvent,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

vi.mock("src/api/agents/connectedAgents", () => ({
  useConnectedAgents: () => ({
    data: {
      items: [
        { id: "a1", name: "ShopMate", modality: "text", source: "retell" },
      ],
      total: 1,
    },
    isLoading: false,
  }),
}));

vi.mock("src/api/agent-definition/agent-definition-version", () => ({
  useAgentDefinitionVersions: ({ selectedAgentId }) => ({
    data: selectedAgentId
      ? { pages: [{ results: [{ id: "version-1", is_active: true }] }] }
      : undefined,
    isLoading: false,
  }),
}));

vi.mock("src/api/harness/releases", () => ({
  useHarnessReleaseFit: vi.fn(),
  createHarnessAdaptation: vi.fn(),
}));

vi.mock("src/api/harness/harness", () => ({
  harnessIdempotencyKey: () => "idem-test",
}));

// The adaptation as its job reports it: "run-job" also started its run, "lost-job" failed.
vi.mock("src/api/simulate-environments/environment", () => ({
  harnessJobQuery: (id) => ({
    queryKey: ["harness-job", id],
    queryFn: async () =>
      id === "lost-job"
        ? {
            job: { metadata: {} },
            status: {
              state: "failed",
              failure: {
                message:
                  "provider sandbox disappeared before terminal delivery",
              },
            },
            conversation: { events: [] },
          }
        : {
            job: {
              metadata: {
                adaptation_result: {
                  composition_id: "env-1",
                  ...(id === "run-job"
                    ? {
                        run: {
                          job_id: "run-1",
                          run_test_id: "rt-1",
                          test_execution_id: "te-1",
                        },
                      }
                    : {}),
                },
              },
            },
            status: { state: "completed" },
            conversation: { events: [] },
          },
  }),
}));

const { useHarnessReleaseFit, createHarnessAdaptation } = await import(
  "src/api/harness/releases"
);
const { default: ReadyEnvironmentPanel } = await import(
  "../starts/ReadyEnvironmentPanel"
);
const { readyEnvironmentFrom } = await import("../readyEnvironments");

const world = readyEnvironmentFrom({
  id: "rel-1",
  digest: "sha256:" + "ab".repeat(32),
  name: "retail",
  system: true,
  parent: null,
  tools: [
    { name: "verify_order_owner", description: "", mutates_state: false },
    { name: "get_order", description: "", mutates_state: false },
    { name: "create_draft", description: "", mutates_state: true },
  ],
  policy: { parameters: [], agent_rules: [] },
  suites: [
    {
      id: "suite-1",
      digest: "sha256:" + "cd".repeat(32),
      name: "retail_base",
      scenario_count: 3,
    },
  ],
  catalogue: {
    title: "Retail",
    summary: "An online clothing store.",
    channel: "chat",
    records: { orders: 38 },
    apps: [],
    example_requests: [],
  },
});

const FIT = {
  agent: { name: "ShopMate", version: "v1" },
  agent_tools: [{ name: "check_owner" }, { name: "order_details" }],
  rows: [
    { world: "verify_order_owner", agent: "check_owner", kind: "mapped" },
    { world: "get_order", agent: "order_details", kind: "mapped" },
    { world: "create_draft", agent: null, kind: "missing" },
  ],
  platform_actions: [{ name: "end_call", description: "End the call." }],
  total: 3,
  unmeasured: [{ key: "reply", title: "Reply by email" }],
};

const NO_MATCH = {
  ...FIT,
  rows: FIT.rows.map((row) => ({ ...row, agent: null, kind: "missing" })),
  unmeasured: [
    { key: "verify", title: "Verify the owner" },
    { key: "lookup", title: "Look up an order" },
    { key: "reply", title: "Reply by email" },
  ],
};

const render = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  baseRender(
    <QueryClientProvider client={client}>
      <ReadyEnvironmentPanel item={{ world, initialTab: "start" }} />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("radio", { name: "ShopMate" }));
};

const run = () => screen.getByRole("button", { name: /^Run the/ });
const PAIRED = {
  verify_order_owner: "check_owner",
  get_order: "order_details",
  create_draft: null,
};

beforeEach(() => {
  navigate.mockReset();
  createHarnessAdaptation.mockReset();
  useHarnessReleaseFit.mockReset();
  useHarnessReleaseFit.mockReturnValue({ data: FIT, isError: false });
});

describe("Use with my agent", () => {
  it("pairs each world tool with the agent's and counts what it measures", () => {
    render();

    expect(useHarnessReleaseFit).toHaveBeenLastCalledWith({
      suiteId: "suite-1",
      agentVersionId: "version-1",
      pairing: null,
    });
    expect(screen.getByText("Partial")).toBeInTheDocument();
    expect(
      screen.getByText(
        /1 of 3 cases can't be measured\. They stay listed, never dropped\./,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "A case can only be graded if your agent has the tool it needs — pair each world tool with yours.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("World tool")).toBeInTheDocument();
    expect(screen.getByText("ShopMate's tool")).toBeInTheDocument();
    // Paired tools fold into one row; the unpaired ones stay out, Not available.
    expect(
      screen.getByRole("button", { name: "2 matched" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("combobox", {
        name: "ShopMate's tool for create_draft",
      }),
    ).toHaveTextContent("Not available");
    expect(
      screen.queryByRole("combobox", {
        name: "ShopMate's tool for get_order",
      }),
    ).toBeNull();
    expect(
      screen.getByText(
        /Runs for real on ShopMate's platform: end_call \(end the call\)/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Live: only the starting state is checked."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("1 case can't be measured with ShopMate"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "ShopMate is tested live and calls its own tools; this world only grades it. Test a simulated copy for simulated tools.",
      ),
    ).toBeInTheDocument();
    expect(run()).toHaveTextContent("Run the 2 measurable cases");
    expect(run()).toBeEnabled();
  });

  it("shows the matched pairs on request and counts a changed pairing again", () => {
    render();

    fireEvent.click(screen.getByRole("button", { name: "2 matched" }));
    fireEvent.mouseDown(
      screen.getByRole("combobox", { name: "ShopMate's tool for get_order" }),
    );
    fireEvent.click(
      within(screen.getByRole("listbox")).getByText("Not available"),
    );

    expect(useHarnessReleaseFit).toHaveBeenLastCalledWith({
      suiteId: "suite-1",
      agentVersionId: "version-1",
      pairing: { ...PAIRED, get_order: null },
    });
  });

  it("moves an agent tool to the world tool it is picked for", () => {
    render();

    fireEvent.mouseDown(
      screen.getByRole("combobox", {
        name: "ShopMate's tool for create_draft",
      }),
    );
    fireEvent.click(
      within(screen.getByRole("listbox")).getByText("check_owner"),
    );

    expect(useHarnessReleaseFit).toHaveBeenLastCalledWith({
      suiteId: "suite-1",
      agentVersionId: "version-1",
      pairing: {
        verify_order_owner: null,
        get_order: "order_details",
        create_draft: "check_owner",
      },
    });
  });

  it("says nothing can be measured when no tool matches", () => {
    useHarnessReleaseFit.mockReturnValue({ data: NO_MATCH, isError: false });
    render();

    expect(screen.getByText("Can't be measured")).toBeInTheDocument();
    expect(
      screen.getByText(
        /ShopMate has none of this world's tools\. Pair one below, or choose another world\./,
      ),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Not available")).toHaveLength(3);
    expect(
      screen.getByText("3 cases can't be measured with ShopMate"),
    ).toBeInTheDocument();
    expect(run()).toHaveTextContent("Run the same cases");
    expect(run()).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Adapt to my agent" }),
    ).toBeEnabled();
  });

  it("opens the design at once when adapting to the agent", async () => {
    createHarnessAdaptation.mockResolvedValue({ job: { job_id: "adapt-job" } });
    render();

    fireEvent.click(screen.getByRole("button", { name: "Adapt to my agent" }));

    await waitFor(() => expect(navigate).toHaveBeenCalled());
    expect(createHarnessAdaptation).toHaveBeenCalledWith({
      suite_id: "suite-1",
      agent_version_id: "version-1",
      name: "Retail · ShopMate",
      idempotency_key: "idem-test",
      pairing: PAIRED,
      mode: "review",
    });
    expect(navigate.mock.calls[0][0]).toMatch(/adapt-job$/);
    expect(
      screen.queryByRole("status", { name: "Preparing your run" }),
    ).toBeNull();
  });

  it("runs the measurable cases straight away, then opens the run", async () => {
    createHarnessAdaptation.mockResolvedValue({ job: { job_id: "run-job" } });
    render();

    fireEvent.click(run());

    expect(
      await screen.findByRole("status", { name: "Preparing your run" }),
    ).toBeInTheDocument();
    expect(createHarnessAdaptation).toHaveBeenCalledWith(
      expect.objectContaining({ mode: "run", pairing: PAIRED }),
    );
    await waitFor(() => expect(navigate).toHaveBeenCalled());
    expect(navigate.mock.calls[0][0]).toMatch(/env-1.*rt-1.*te-1/);
  });

  it("says why preparing the run failed and lets the person try again", async () => {
    createHarnessAdaptation.mockResolvedValue({ job: { job_id: "lost-job" } });
    render();

    fireEvent.click(run());

    expect(
      await screen.findByText(
        "provider sandbox disappeared before terminal delivery",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("status", { name: "Preparing your run" }),
    ).toBeNull();
    expect(run()).toBeEnabled();
    expect(navigate).not.toHaveBeenCalled();
  });
});
