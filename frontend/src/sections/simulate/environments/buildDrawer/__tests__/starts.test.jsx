import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  render as baseRender,
  fireEvent,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";

/*
  The starts in Build environment: from scratch, from code, prebuilt worlds,
  the SaaS sandbox, an MCP world and your agents. None has an API yet, so each
  shows its whole design and its last step only says it's UI only.
*/

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

vi.mock("notistack", async () => {
  const actual = await vi.importActual("notistack");
  return { ...actual, enqueueSnackbar: vi.fn() };
});

const { createHarnessJob, preflightHarnessJob } = await import(
  "src/api/harness/harness"
);
const { enqueueSnackbar } = await import("notistack");
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
const header = () =>
  screen.getByRole("button", { name: /^Back to / }).parentElement;

// The last step says it's UI only, and nothing reaches the build API.
const expectUiOnlyToast = () => {
  expect(enqueueSnackbar).toHaveBeenCalledWith(
    expect.stringMatching(/^UI only: .* isn't wired to the API yet\.$/),
    { variant: "info" },
  );
  expect(createHarnessJob).not.toHaveBeenCalled();
  expect(preflightHarnessJob).not.toHaveBeenCalled();
};

beforeEach(() => {
  agentItems = TWO_AGENTS;
  enqueueSnackbar.mockClear();
  createHarnessJob.mockReset();
  preflightHarnessJob.mockReset();
});

describe("Build from scratch", () => {
  it("opens from the header, UI only, and builds once there's enough to go on", () => {
    render();
    click(/Build from scratch/);
    expect(
      within(header()).getByText("Build from scratch"),
    ).toBeInTheDocument();
    expect(within(header()).getByText("UI only")).toBeInTheDocument();
    expect(
      within(header()).getByText(/Describe the world to test in/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: "Environment name" }),
    ).toBeInTheDocument();

    const build = button("Build environment");
    expect(build).toBeDisabled();
    const flow = screen.getByRole("textbox", {
      name: "Describe the flow, or drop files to learn from",
    });
    fireEvent.change(flow, { target: { value: "short" } });
    expect(build).toBeDisabled();
    expect(screen.getByText("1 word")).toBeInTheDocument();

    click("It escalates if…");
    expect(flow).toHaveValue("short It escalates if…");
    expect(screen.getByText("4 words")).toBeInTheDocument();
    expect(build).toBeEnabled();
    fireEvent.click(build);
    expectUiOnlyToast();
  });

  it("builds from an attached file alone, listed and removable", () => {
    render();
    click(/Build from scratch/);
    const file = new File(["a,b"], "calls.csv", { type: "text/csv" });
    fireEvent.change(screen.getByTestId("describe-files"), {
      target: { files: [file] },
    });
    const list = screen.getByRole("list", { name: "Attached files" });
    expect(within(list).getByText("calls.csv")).toBeInTheDocument();
    expect(button("Build environment")).toBeEnabled();
    click("Remove calls.csv");
    expect(screen.queryByRole("list", { name: "Attached files" })).toBeNull();
    expect(button("Build environment")).toBeDisabled();
  });

  it("shows the Falcon AI bar and says drafting isn't wired, without drafting", () => {
    render();
    click(/Build from scratch/);
    click("Write with Falcon AI");
    const ask = screen.getByRole("textbox", { name: "Ask Falcon AI" });
    fireEvent.change(ask, { target: { value: "triages GitHub issues" } });
    fireEvent.keyDown(ask, { key: "Enter" });
    expect(
      screen.getByText(/Falcon AI drafting isn't wired to the API yet/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("textbox", {
        name: "Describe the flow, or drop files to learn from",
      }),
    ).toHaveValue("");
    click("Voice input");
    expectUiOnlyToast();
  });
});

describe("Build from code", () => {
  it("opens its setup with the CLI steps, per OS, and the Python and coding agent ways", () => {
    render();
    fireEvent.click(
      within(
        screen.getByRole("listitem", { name: "Build from code" }),
      ).getByRole("button", { name: "Open setup" }),
    );
    expect(within(header()).getByText("From code")).toBeInTheDocument();
    expect(within(header()).getByText("UI only")).toBeInTheDocument();
    expect(screen.getByText("pip install futureagi")).toBeInTheDocument();
    expect(screen.getByText("fai env up my-environment")).toBeInTheDocument();
    click("Windows");
    expect(screen.getByText("py -m pip install futureagi")).toBeInTheDocument();

    click("Python");
    expect(
      screen.getByText(/from fi\.simulate import Environment/),
    ).toBeInTheDocument();
    expect(button("Copy code")).toBeInTheDocument();
    expect(screen.getByText(/marked Managed in code/)).toBeInTheDocument();

    click("Your coding agent");
    expect(
      screen.getByText(
        "claude mcp add futureagi --transport http https://api.futureagi.com/mcp",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Build an environment for my refund agent/),
    ).toBeInTheDocument();
  });
});

describe("Prebuilt worlds", () => {
  it("opens a world: try it now with the template's own counts", async () => {
    render();
    fireEvent.click(
      await screen.findByRole("button", { name: "Open Customer Support Line" }),
    );
    expect(
      within(header()).getByText("Customer Support Line"),
    ).toBeInTheDocument();
    expect(within(header()).getByText("Voice")).toBeInTheDocument();
    expect(within(header()).getByText("UI only")).toBeInTheDocument();
    // 500 + 200 + 85 + 340 seeded rows, 8 tools, 6 rules, 4 evaluations.
    expect(screen.getByText("1,125 seeded rows")).toBeInTheDocument();
    expect(screen.getByText("8 the world answers")).toBeInTheDocument();
    expect(screen.getByText("6 graded on every run")).toBeInTheDocument();
    expect(
      screen.getByText(/scenarios, already proven, and 4 evaluations/),
    ).toBeInTheDocument();
    expect(
      screen.getByText("The world never touches production."),
    ).toBeInTheDocument();
    click("Try it now");
    expectUiOnlyToast();
  });

  it("uses a world with one of your agents of its type, in Future AGI or on your machine", async () => {
    render();
    fireEvent.click(
      await screen.findByRole("button", { name: "Open Customer Support Line" }),
    );
    fireEvent.click(
      screen.getByRole("radio", { name: "Use it with your agent" }),
    );
    expect(
      screen.getByRole("radio", { name: "Billing line" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "Returns chat" })).toBeNull();
    expect(button("Build environment")).toBeDisabled();
    fireEvent.click(screen.getByRole("radio", { name: "Billing line" }));
    click("Build environment");
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("textbox", { name: "Name" })).toHaveValue(
      "Billing line environment",
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    fireEvent.click(screen.getByRole("radio", { name: "On your machine" }));
    expect(
      screen.getByText(
        'fai env init billing-line-customer-support-line --world env-voice-support --agent "Billing line" --version v3 --scenarios 10',
      ),
    ).toBeInTheDocument();
    click("Open the build");
    expectUiOnlyToast();
  });

  it("browses every world, filters them, and goes back where it came from", async () => {
    render();
    // Nine worlds the fixture offers for voice, chat or computer use, and the sandbox.
    fireEvent.click(
      await screen.findByRole("button", { name: "Browse all 10" }),
    );
    expect(screen.getByText("Prebuilt worlds")).toBeInTheDocument();
    expect(screen.getByText("10 of 10 worlds")).toBeInTheDocument();
    const worlds = screen.getByRole("list", { name: "Prebuilt worlds" });
    expect(
      within(worlds).getByRole("listitem", { name: "SaaS sandbox" }),
    ).toBeInTheDocument();
    // Computer use worlds are listed but can't be opened yet.
    expect(button("Open Browser")).toBeDisabled();

    const search = screen.getByRole("textbox", { name: "Search worlds" });
    fireEvent.change(search, { target: { value: "nothing-like-this" } });
    expect(screen.getByText("No world matches")).toBeInTheDocument();
    click("Clear filters");
    expect(search).toHaveValue("");

    fireEvent.change(search, { target: { value: "banking" } });
    expect(screen.getByText("1 of 10 worlds")).toBeInTheDocument();
    click("Open Retail Banking Assistant");
    expect(
      within(header()).getByText("Retail Banking Assistant"),
    ).toBeInTheDocument();
    click("Back to prebuilt worlds");
    expect(screen.getByText("Prebuilt worlds")).toBeInTheDocument();
    click("Back to build environment");
    expect(screen.getByText("Build environment")).toBeInTheDocument();
  });
});

describe("SaaS sandbox", () => {
  it("picks apps and an agent, then sets up the sandboxes, UI only", async () => {
    render();
    click("SaaS sandbox");
    expect(within(header()).getByText("SaaS sandbox")).toBeInTheDocument();
    expect(within(header()).getByText("UI only")).toBeInTheDocument();
    expect(screen.getByText("PRODUCTIVITY & DOCS")).toBeInTheDocument();
    expect(button("Pick at least one app")).toBeDisabled();

    fireEvent.click(screen.getByRole("checkbox", { name: "Slack" }));
    expect(button("Pick the agent")).toBeDisabled();
    expect(
      screen.getByPlaceholderText(/Slack workspace with #support-urgent/),
    ).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Slack sandbox")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "Returns chat" }));
    expect(button("Set up 1 sandbox")).toBeEnabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "Gmail" }));
    expect(screen.getByPlaceholderText("2-app sandbox")).toBeInTheDocument();
    click("Set up 2 sandboxes");
    expectUiOnlyToast();
  });
});

describe("MCP world", () => {
  it("picks the agent and a sample Gateway server, or another server's URL", () => {
    render();
    click("Use them as an agent's world");
    expect(within(header()).getByText("MCP server")).toBeInTheDocument();
    expect(within(header()).getByText("UI only")).toBeInTheDocument();
    expect(
      screen.getByText("Pick the agent this environment is for"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "Billing line" }));
    expect(
      screen.getByText("Pick a server from your Gateway"),
    ).toBeInTheDocument();
    expect(button(/Linear/)).toBeDisabled();
    fireEvent.click(button(/Shopify/));
    expect(within(button(/Shopify/)).getByText(/Sample/)).toBeInTheDocument();

    click("Another server");
    expect(button(/stdio/)).toBeDisabled();
    expect(
      within(button(/stdio/)).getByText("On your machine only"),
    ).toBeInTheDocument();
    expect(button("Build environment")).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "Server URL" }), {
      target: { value: "https://mcp.acme.com/sse" },
    });
    click("Build environment");
    expectUiOnlyToast();
  });
});

describe("Your agents", () => {
  it("opens Build environment for an agent: name, version, scenarios, preflight", async () => {
    render();
    click("Build environment for Returns chat");
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("UI only")).toBeInTheDocument();
    // Two environments already, so the name is numbered after them.
    expect(within(dialog).getByRole("textbox", { name: "Name" })).toHaveValue(
      "Returns chat environment 3",
    );
    expect(
      within(dialog).getByRole("combobox", { name: "Built from version" }),
    ).toHaveTextContent("v3");
    fireEvent.click(within(dialog).getByRole("button", { name: "25" }));
    expect(
      within(dialog).getByRole("spinbutton", {
        name: "Scenarios to start with",
      }),
    ).toHaveValue(25);
    expect(within(dialog).getByRole("button", { name: "25" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    const build = within(dialog).getByRole("button", {
      name: "Build environment",
    });
    expect(build).toBeDisabled();
    expect(within(dialog).getByText("Run preflight first")).toBeInTheDocument();
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Run preflight" }),
    );
    await within(dialog).findByText("Ready to build", {}, { timeout: 3000 });
    expect(within(dialog).getByText("Sandbox")).toBeInTheDocument();
    expect(build).toBeEnabled();
    fireEvent.click(build);
    expectUiOnlyToast();
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
