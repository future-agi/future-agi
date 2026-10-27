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
  it("opens on every way in, Code first", () => {
    render();
    expect(screen.getByText("Build environment")).toBeInTheDocument();
    for (const name of [
      "Use a prebuilt world",
      "Connect your agent",
      "Your agents",
    ]) {
      expect(screen.getByRole("heading", { name })).toBeInTheDocument();
    }
    expect(screen.getByRole("tab", { name: /Code 22/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(row("GitHub")).toBeInTheDocument();
    expect(screen.queryByRole("listitem", { name: "Vapi" })).toBeNull();
  });

  it("lists each tab's ways", () => {
    render();
    fireEvent.click(screen.getByRole("tab", { name: /Hosted platforms 9/ }));
    expect(row("Vapi")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /Other ways 7/ }));
    expect(row("Phone number")).toBeInTheDocument();
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

  it("shows three prebuilt worlds and the SaaS sandbox, UI only", async () => {
    render();
    expect(
      await screen.findByRole("listitem", { name: "Customer Support Line" }),
    ).toBeInTheDocument();
    const sandbox = screen.getByRole("button", { name: "SaaS sandbox" });
    expect(within(sandbox).getByText("UI only")).toBeInTheDocument();
    expect(within(sandbox).queryByText("Coming soon")).toBeNull();
  });

  it("lists the connected agents, filtered by type", () => {
    render();
    expect(row("Billing line")).toHaveTextContent("Vapi · v3");
    expect(row("Returns chat")).toHaveTextContent("GitHub · No version yet");
    fireEvent.click(screen.getByRole("button", { name: /^Voice 1/ }));
    expect(screen.queryByRole("listitem", { name: "Returns chat" })).toBeNull();
  });

  it("offers every start, flagging the ones without an API as UI only", () => {
    render();
    expect(
      screen.getByRole("button", { name: /Build from scratch/ }),
    ).toBeEnabled();
    const code = row("Build from code");
    expect(within(code).getByText("UI only")).toBeInTheDocument();
    expect(within(code).queryByText("Coming soon")).toBeNull();
    expect(
      within(code).getByRole("button", { name: "Open setup" }),
    ).toBeEnabled();
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
    expect(screen.getByText("Repository link")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Connect GitHub" })).toBeEnabled();
    fireEvent.click(
      screen.getByRole("button", { name: "Back to build environment" }),
    );
    expect(row("GitHub")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("opens Vapi locked to Vapi, chat UI only", () => {
    render();
    fireEvent.click(screen.getByRole("tab", { name: /Hosted platforms/ }));
    connect("Vapi");
    expect(screen.getByText("Vapi API key")).toBeInTheDocument();
    const chat = screen.getByRole("button", { name: /Chat/ });
    expect(chat).toBeEnabled();
    expect(within(chat).getByText("UI only")).toBeInTheDocument();
  });

  it("can't be closed while the build is being created", async () => {
    preflightHarnessJob.mockResolvedValue({
      ready_to_submit: true,
      credentials: { scanned_files: 3, requirements: [], probe: [] },
    });
    createHarnessJob.mockReturnValue(new Promise(() => {}));
    const onClose = render();
    connect("GitHub");
    fireEvent.change(screen.getByRole("textbox", { name: "Repository link" }), {
      target: { value: "acme/agent" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Run preflight" }));
    await screen.findByText("Ready to build");
    fireEvent.click(
      screen.getByRole("button", { name: /Connect and build environment/ }),
    );
    await screen.findByText("Building the environment…");
    expect(
      screen.getByRole("button", { name: "Back to build environment" }),
    ).toBeDisabled();
    fireEvent.keyDown(screen.getByRole("presentation"), { key: "Escape" });
    await waitFor(() =>
      expect(screen.getByText("Repository link")).toBeInTheDocument(),
    );
    expect(onClose).not.toHaveBeenCalled();
  });
});
