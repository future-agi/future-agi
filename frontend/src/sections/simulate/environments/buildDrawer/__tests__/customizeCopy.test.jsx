import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import useSimulationAccess from "../../hooks/useSimulationAccess";
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
  useHarnessReleaseFit: vi.fn(() => ({ data: null, isError: false })),
  createHarnessAdaptation: vi.fn(),
  createHarnessCustomization: vi.fn(),
}));

vi.mock("src/api/harness/harness", () => ({
  harnessIdempotencyKey: () => "idem-copy",
}));

const { createHarnessCustomization } = await import("src/api/harness/releases");
const { default: ReadyEnvironmentPanel } = await import(
  "../starts/ReadyEnvironmentPanel"
);
const { readyEnvironmentFrom } = await import("../readyEnvironments");

const RELEASE = {
  id: "rel-1",
  digest: "sha256:" + "ab".repeat(32),
  name: "retail",
  system: true,
  parent: null,
  tools: [{ name: "get_order", description: "", mutates_state: false }],
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
  derived_from: null,
};
const world = readyEnvironmentFrom(RELEASE);

const render = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  baseRender(
    <QueryClientProvider client={client}>
      <ReadyEnvironmentPanel item={{ world, initialTab: "customize" }} />
    </QueryClientProvider>,
  );
};
const build = () => screen.getByRole("button", { name: "Build my copy" });
const change = () =>
  screen.getByRole("textbox", { name: "What should change" });

beforeEach(() => {
  navigate.mockReset();
  createHarnessCustomization.mockReset();
  createHarnessCustomization.mockResolvedValue({ job: { job_id: "copy-job" } });
  vi.mocked(useSimulationAccess).mockReturnValue({
    canEdit: true,
    canRun: true,
  });
});

describe("Customize a private copy", () => {
  it("needs an agent and a change before it builds", () => {
    render();

    expect(build()).toBeDisabled();
    expect(
      screen.getByText(
        "Choose the agent whose workspace the copy is built in.",
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "ShopMate" }));
    expect(build()).toBeDisabled();
    expect(
      screen.getByText("Say what should change in the copy."),
    ).toBeInTheDocument();
    fireEvent.change(change(), { target: { value: "   " } });
    expect(build()).toBeDisabled();
  });

  it("starts the copy with the change and opens its design", async () => {
    render();
    fireEvent.click(screen.getByRole("radio", { name: "ShopMate" }));
    fireEvent.change(change(), {
      target: { value: "  Refunds over $500 need a supervisor " },
    });

    fireEvent.click(build());

    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith(
        "/dashboard/simulate/environments/copy-job",
      ),
    );
    expect(createHarnessCustomization).toHaveBeenCalledWith({
      suite_id: "suite-1",
      agent_version_id: "version-1",
      name: "Retail copy",
      change: "Refunds over $500 need a supervisor",
      idempotency_key: "idem-copy",
    });
  });

  it("says why a copy wasn't started and keeps the change", async () => {
    createHarnessCustomization.mockRejectedValue({
      error: "customization_unsupported",
      message:
        "only a ready environment that extends no other can be customized",
    });
    render();
    fireEvent.click(screen.getByRole("radio", { name: "ShopMate" }));
    fireEvent.change(change(), { target: { value: "Add a loyalty tier" } });

    fireEvent.click(build());

    expect(
      await screen.findByText(/extends no other can be customized/),
    ).toBeInTheDocument();
    expect(change()).toHaveValue("Add a loyalty tier");
    expect(navigate).not.toHaveBeenCalled();
    expect(build()).toBeEnabled();
  });

  it("a viewer can't build a copy", () => {
    vi.mocked(useSimulationAccess).mockReturnValue({
      canEdit: false,
      canRun: false,
    });
    render();
    fireEvent.click(screen.getByRole("radio", { name: "ShopMate" }));
    fireEvent.change(change(), { target: { value: "Add a loyalty tier" } });

    expect(build()).toBeDisabled();
  });
});

describe("a private copy in the ready environments", () => {
  it("reads as the world it was made from, and is private", () => {
    const copy = readyEnvironmentFrom({
      ...RELEASE,
      id: "rel-2",
      name: "retail_copy_ab12cd",
      system: false,
      catalogue: null,
      derived_from: {
        digest: RELEASE.digest,
        name: "retail",
        title: "Retail",
      },
    });

    expect(copy.name).toBe("Retail · copy");
    expect(copy.copyOf).toBe("Retail");
    expect(copy.system).toBe(false);
    // Sealed without catalogue metadata: its rows aren't counted, so none are claimed.
    expect(copy.recordCount).toBeNull();
    expect(readyEnvironmentFrom(RELEASE).recordCount).toBeGreaterThan(0);
  });
});
