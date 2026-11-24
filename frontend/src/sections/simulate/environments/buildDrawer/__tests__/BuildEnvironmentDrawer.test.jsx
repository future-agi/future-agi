import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  render as baseRender,
  fireEvent,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => vi.fn() };
});

vi.mock("src/api/agents/connectedAgents", () => ({
  useConnectedAgents: () => ({
    data: {
      items: [
        {
          id: "a1",
          name: "Billing line",
          modality: "voice",
          source: "vapi",
          latestLabel: "v3",
        },
        {
          id: "a2",
          name: "Returns chat",
          modality: "text",
          source: "github",
          latestLabel: null,
        },
      ],
      total: 2,
    },
    isLoading: false,
  }),
}));

vi.mock("src/api/harness/releases", () => ({
  useHarnessReleases: () => ({
    data: [
      {
        id: "rel-1",
        digest: "sha256:" + "ab".repeat(32),
        name: "retail",
        system: true,
        parent: null,
        tools: [{ name: "get_order", description: "", mutates_state: false }],
        policy: {
          parameters: [],
          agent_rules: [{ id: "verify_first", text: "Verify first." }],
        },
        suites: [
          {
            id: "suite-1",
            digest: "sha256:" + "cd".repeat(32),
            name: "retail_base",
            scenario_count: 100,
            origins: { base: 100, specialized: 0, custom: 0 },
            policy_profile: {},
          },
        ],
        catalogue: {
          title: "Retail",
          summary: "An online clothing store.",
          channel: "chat",
          records: { orders: 38 },
          apps: [],
          example_requests: ["Where is my order?"],
        },
      },
    ],
    isLoading: false,
  }),
  createHarnessAdaptation: vi.fn(),
  useHarnessReleaseFit: () => ({}),
}));

vi.mock("src/api/harness/harness", () => ({
  preflightHarnessJob: vi.fn(),
  createHarnessJob: vi.fn(),
  harnessIdempotencyKey: () => "idem-test",
  getHarnessJob: vi.fn(),
  listHarnessJobs: vi.fn(),
  uploadHarnessSource: vi.fn(),
}));

const { preflightHarnessJob, createHarnessJob } = await import(
  "src/api/harness/harness"
);
const { default: BuildEnvironmentDrawer } = await import(
  "../BuildEnvironmentDrawer"
);
const { resetEnvironmentsStore } = await import(
  "../../store/useEnvironmentsStore"
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

const row = (name) => screen.getByRole("listitem", { name });
const connect = (name) =>
  fireEvent.click(screen.getByRole("button", { name: `Connect ${name}` }));

beforeEach(() => {
  resetEnvironmentsStore();
  preflightHarnessJob.mockReset();
  createHarnessJob.mockReset();
});

describe("BuildEnvironmentDrawer catalog", () => {
  it("opens on the live ways in, Code first", () => {
    render();
    expect(screen.getByText("Build environment")).toBeInTheDocument();
    for (const name of ["Connect your agent", "Your agents"]) {
      expect(screen.getByRole("heading", { name })).toBeInTheDocument();
    }
    expect(
      screen.queryByRole("heading", { name: "Use a prebuilt world" }),
    ).toBeNull();
    expect(screen.getByRole("tab", { name: /Code 2/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(row("GitHub")).toBeInTheDocument();
    expect(screen.queryByRole("listitem", { name: "Vapi" })).toBeNull();
  });

  it("lists each tab's ways", () => {
    render();
    fireEvent.click(screen.getByRole("tab", { name: /Hosted platforms 2/ }));
    expect(row("Vapi")).toBeInTheDocument();
    expect(row("Retell AI")).toBeInTheDocument();
    expect(screen.queryByRole("listitem", { name: "ElevenLabs" })).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: /Other ways 2/ }));
    expect(row("LiveKit project")).toBeInTheDocument();
    expect(row("Phone number")).toBeInTheDocument();
    expect(
      screen.queryByRole("listitem", { name: "Agent endpoint" }),
    ).toBeNull();
  });

  it("offers Connect only where the build API can build, named for the way", () => {
    render();
    expect(
      screen.getByRole("button", { name: "Connect GitHub" }),
    ).toBeInTheDocument();
    expect(within(row("GitHub")).queryByText("UI only")).toBeNull();
  });

  it("searches across every tab and says when nothing matches", () => {
    render();
    const search = screen.getByRole("textbox", {
      name: "Search ways to connect",
    });
    fireEvent.change(search, { target: { value: "retell" } });
    expect(row("Retell AI")).toBeInTheDocument();
    expect(screen.queryByRole("listitem", { name: "GitHub" })).toBeNull();
    fireEvent.change(search, { target: { value: "nothing-like-this" } });
    expect(screen.getByText(/No way to connect matches/)).toBeInTheDocument();
  });

  it("lists the published ready environments, each with its two ways in", () => {
    render();
    const card = screen.getByRole("listitem", { name: "Retail" });
    expect(card).toHaveTextContent("100 scenarios · 1 tools");
    expect(
      within(card).getByRole("button", { name: "Use with my agent" }),
    ).toBeEnabled();
    expect(
      within(card).getByRole("button", { name: "Customize" }),
    ).toBeEnabled();
  });

  it("lists the connected agents, filtered by type", () => {
    render();
    expect(row("Billing line")).toHaveTextContent("Vapi · v3");
    expect(row("Returns chat")).toHaveTextContent("GitHub · No version yet");
    fireEvent.click(screen.getByRole("button", { name: /^Voice 1/ }));
    expect(screen.queryByRole("listitem", { name: "Returns chat" })).toBeNull();
  });

  it("hides Build from code", () => {
    render();
    expect(
      screen.queryByRole("listitem", { name: "Build from code" }),
    ).toBeNull();
  });

  it("closes from its own close button", () => {
    const onClose = render();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalled();
  });
});

describe("BuildEnvironmentDrawer setup", () => {
  it("goes back to the tab it was opened from", () => {
    render();
    fireEvent.click(screen.getByRole("tab", { name: /Hosted platforms/ }));
    connect("Vapi");
    fireEvent.click(
      screen.getByRole("button", { name: "Back to build environment" }),
    );
    expect(
      screen.getByRole("tab", { name: /Hosted platforms/ }),
    ).toHaveAttribute("aria-selected", "true");
  });

  it("has a back arrow to the list, and an X that closes the whole drawer", () => {
    const onClose = render();
    connect("GitHub");
    fireEvent.click(
      screen.getByRole("button", { name: "Back to build environment" }),
    );
    expect(row("GitHub")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    connect("GitHub");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalled();
  });

  it("opens a way's setup in the drawer, and its close goes back to the list", () => {
    const onClose = render();
    connect("GitHub");
    expect(screen.getByText("Paste a repo link")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Find agents/ })).toBeDisabled();
    fireEvent.click(
      screen.getByRole("button", { name: "Back to build environment" }),
    );
    expect(row("GitHub")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("opens Vapi on the prod form, without the type and platform pickers", () => {
    render();
    fireEvent.click(screen.getByRole("tab", { name: /Hosted platforms/ }));
    connect("Vapi");
    expect(screen.getByText("Vapi API key")).toBeInTheDocument();
    expect(screen.queryByText("Agent type")).toBeNull();
    expect(screen.queryByText("Platform")).toBeNull();
  });

  it("can't be closed while the build is being created", async () => {
    preflightHarnessJob.mockResolvedValue({
      ready_to_submit: true,
      credentials: { scanned_files: 3, requirements: [], probe: [] },
    });
    createHarnessJob.mockReturnValue(new Promise(() => {}));
    const onClose = render();
    fireEvent.click(screen.getByRole("tab", { name: /Hosted platforms/ }));
    connect("Vapi");
    fireEvent.change(screen.getByPlaceholderText("asst_9f2c…"), {
      target: { value: "asst_1" },
    });
    fireEvent.change(screen.getByPlaceholderText("sk-…"), {
      target: { value: "sk-test" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Run preflight" }));
    await screen.findByText("Ready to build");
    fireEvent.click(screen.getByRole("button", { name: /Build environment/ }));
    await screen.findByText("Building…");
    expect(
      screen.getByRole("button", { name: "Back to build environment" }),
    ).toBeDisabled();
    fireEvent.keyDown(screen.getByRole("presentation"), { key: "Escape" });
    await waitFor(() =>
      expect(screen.getByText("Vapi API key")).toBeInTheDocument(),
    );
    expect(onClose).not.toHaveBeenCalled();
  });
});
