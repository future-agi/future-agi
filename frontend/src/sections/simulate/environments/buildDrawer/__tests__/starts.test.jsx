vi.mock("src/pages/dashboard/harness/DesignForm", () => ({
  default: () => <div>Server environment design form</div>,
}));
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  render as baseRender,
  fireEvent,
  screen,
  within,
} from "src/utils/test-utils";

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => vi.fn() };
});

const agent = (id, name, modality, extra = {}) => ({
  id,
  name,
  modality,
  source: modality === "voice" ? "vapi" : "github",
  latestLabel: "v3",
  environmentCount: 0,
  ...extra,
});
const TWO_AGENTS = [
  agent("a1", "Billing line", "voice"),
  agent("a2", "Returns chat", "text", { environmentCount: 2 }),
];
let agentItems = TWO_AGENTS;

vi.mock("src/api/agents/connectedAgents", () => ({
  useConnectedAgents: () => ({
    data: { items: agentItems, total: agentItems.length },
    isLoading: false,
  }),
}));

vi.mock("src/api/harness/harness", () => ({
  preflightHarnessJob: vi.fn(),
  storeHarnessSecretValues: vi.fn(),
  createHarnessJob: vi.fn(),
  harnessIdempotencyKey: () => "idem-test",
  getHarnessJob: vi.fn(),
  listHarnessJobs: vi.fn(),
  uploadHarnessSource: vi.fn(),
}));

vi.mock(
  "src/sections/agent-playground/connected/detail/BuildEnvironmentDialog",
  () => ({
    default: ({ agent, open, onClose }) =>
      open ? (
        <div role="dialog" aria-label={`Build ${agent.name}`}>
          <span>{agent.id}</span>
          <button type="button" onClick={onClose}>
            Close dialog
          </button>
        </div>
      ) : null,
  }),
);

const { default: BuildEnvironmentDrawer } = await import(
  "../BuildEnvironmentDrawer"
);

const render = () => {
  const onClose = vi.fn();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  baseRender(
    <QueryClientProvider client={client}>
      <BuildEnvironmentDrawer open onClose={onClose} />
    </QueryClientProvider>,
  );
  return onClose;
};

const button = (name) => screen.getByRole("button", { name });
const click = (name) => fireEvent.click(button(name));
beforeEach(() => {
  agentItems = TWO_AGENTS;
});

describe("Build environment header", () => {
  it("opens the live design form from Build from scratch", () => {
    render();
    click("Build from scratch");
    expect(
      screen.getByText("Server environment design form"),
    ).toBeInTheDocument();
  });
});

describe("Your agents", () => {
  it("opens the agent page's Build environment dialog for the agent picked", async () => {
    render();
    click("Build environment for Returns chat");
    const dialog = await screen.findByRole("dialog", {
      name: "Build Returns chat",
    });
    expect(within(dialog).getByText(TWO_AGENTS[1].id)).toBeInTheDocument();
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Close dialog" }),
    );
    expect(
      screen.queryByRole("dialog", { name: "Build Returns chat" }),
    ).toBeNull();
  });

  it("shows six, then all of them", () => {
    agentItems = Array.from({ length: 8 }, (_, i) =>
      agent(`a${i}`, `Agent ${i}`, i % 2 ? "voice" : "text"),
    );
    render();
    expect(
      screen.queryByRole("button", { name: "Build environment for Agent 7" }),
    ).toBeNull();
    click("Show all 8");
    expect(button("Build environment for Agent 7")).toBeInTheDocument();
    click("Show fewer");
    expect(
      screen.queryByRole("button", { name: "Build environment for Agent 7" }),
    ).toBeNull();
  });
});
