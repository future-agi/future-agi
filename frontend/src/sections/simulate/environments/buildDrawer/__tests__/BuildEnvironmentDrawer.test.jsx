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

vi.mock("src/api/harness/harness", () => ({
  preflightHarnessJob: vi.fn(),
  storeHarnessSecretValues: vi.fn(),
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

const render = (props = {}) => {
  const onClose = vi.fn();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = baseRender(
    <QueryClientProvider client={client}>
      <BuildEnvironmentDrawer open onClose={onClose} {...props} />
    </QueryClientProvider>,
  );
  onClose.unmount = view.unmount;
  return onClose;
};

const row = (name) => screen.getByRole("listitem", { name });
const connect = (name) =>
  fireEvent.click(screen.getByRole("button", { name: `Connect ${name}` }));

beforeEach(() => {
  sessionStorage.clear();
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

  it("hides the prebuilt worlds and shows the SaaS sandbox disabled", () => {
    render();
    expect(
      screen.queryByRole("listitem", { name: "Customer Support Line" }),
    ).toBeNull();
    expect(screen.getByRole("button", { name: "SaaS sandbox" })).toBeDisabled();
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

  it("has a back arrow to the list, and an X that asks before closing", async () => {
    const onClose = render();
    connect("GitHub");
    fireEvent.click(
      screen.getByRole("button", { name: "Back to build environment" }),
    );
    expect(row("GitHub")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    connect("GitHub");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    const dialog = (
      await screen.findByText("Discard this environment?")
    ).closest("[role='dialog']");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByText("Paste a repo link")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    const again = (
      await screen.findByText("Discard this environment?")
    ).closest("[role='dialog']");
    fireEvent.click(within(again).getByRole("button", { name: "Discard" }));
    expect(onClose).toHaveBeenCalled();
  });

  it("says which way it is on, and reopens it with what was typed but no key", () => {
    const onWayChange = vi.fn();
    const first = render({ way: "", onWayChange });
    fireEvent.click(screen.getByRole("tab", { name: /Hosted platforms/ }));
    connect("Vapi");
    expect(onWayChange).toHaveBeenLastCalledWith("vapi");
    fireEvent.change(screen.getByPlaceholderText("asst_9f2c…"), {
      target: { value: "asst_kept" },
    });
    fireEvent.change(screen.getByPlaceholderText("sk-…"), {
      target: { value: "sk-never-saved" },
    });
    expect(JSON.stringify(sessionStorage)).not.toContain("sk-never-saved");
    first.unmount();

    render({ way: "vapi" });
    expect(screen.getByPlaceholderText("asst_9f2c…")).toHaveValue("asst_kept");
    expect(screen.getByPlaceholderText("sk-…")).toHaveValue("");
  });

  it("reopens GitHub on its details with the name, and asks for values again", () => {
    sessionStorage.setItem(
      "environments.build.github.form",
      JSON.stringify({
        picked: {
          source: { repository: "acme/agent", ref: "main" },
          entry: "agent.py",
          envNames: ["ANTHROPIC_API_KEY"],
          name: "acme",
        },
        form: {
          name: "kept name",
          extra: [],
          egress: "",
          secretFiles: [],
          scenarioCount: 5,
          observabilityEnabled: false,
        },
      }),
    );
    render({ way: "github" });
    expect(screen.getByDisplayValue("kept name")).toBeInTheDocument();
    expect(screen.getByText("acme/agent at main")).toBeInTheDocument();
    expect(
      screen.getByText("0 of 1 set", { exact: false }),
    ).toBeInTheDocument();
  });

  it("starts a way afresh when it is picked from the list", () => {
    sessionStorage.setItem(
      "environments.build.vapi.form",
      JSON.stringify({ id: "asst_stale" }),
    );
    render();
    fireEvent.click(screen.getByRole("tab", { name: /Hosted platforms/ }));
    connect("Vapi");
    expect(screen.getByPlaceholderText("asst_9f2c…")).toHaveValue("");
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
