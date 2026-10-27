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
  "Connect a new agent" in an agent picker opens the ways to connect an agent
  of the picker's type as a layer over the setup, then that way's form, which
  only connects. Connecting isn't wired yet, so it adds a sample agent and
  picks it; nothing reaches the build API.
*/

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
          environmentCount: 0,
        },
        {
          id: "a2",
          name: "Returns chat",
          modality: "text",
          source: "github",
          latestLabel: "v1",
          environmentCount: 0,
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

vi.mock("notistack", async () => {
  const actual = await vi.importActual("notistack");
  return { ...actual, enqueueSnackbar: vi.fn() };
});

const { preflightHarnessJob, storeHarnessSecretValues, createHarnessJob } =
  await import("src/api/harness/harness");
const { enqueueSnackbar } = await import("notistack");
const { default: BuildEnvironmentDrawer } = await import(
  "../BuildEnvironmentDrawer"
);
const { default: AgentPicker } = await import("../starts/AgentPicker");

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
const way = (name) => screen.queryByRole("listitem", { name });
const backButton = () => screen.getByRole("button", { name: /^Back to / });
const header = () => backButton().parentElement;

const expectNoBuildApi = () => {
  expect(preflightHarnessJob).not.toHaveBeenCalled();
  expect(storeHarnessSecretValues).not.toHaveBeenCalled();
  expect(createHarnessJob).not.toHaveBeenCalled();
};

const openSandbox = () => {
  render();
  click("SaaS sandbox");
};

const openWorldWithYourAgent = async (name) => {
  render();
  fireEvent.click(await screen.findByRole("button", { name: "Browse all 10" }));
  click(`Open ${name}`);
  fireEvent.click(
    screen.getByRole("radio", { name: "Use it with your agent" }),
  );
};

beforeEach(() => {
  enqueueSnackbar.mockClear();
  preflightHarnessJob.mockReset();
  storeHarnessSecretValues.mockReset();
  createHarnessJob.mockReset();
});

describe("a new layer", () => {
  it("moves focus to its back arrow and opens at its top", () => {
    openSandbox();
    expect(document.activeElement).toBe(button("Back to build environment"));
    expect(backButton().closest(".MuiDrawer-paper")).toBeTruthy();
    click("Connect a new agent");
    expect(document.activeElement).toBe(button("Back to SaaS sandbox"));
    const layer = screen
      .getByRole("textbox", { name: "Search ways to connect" })
      .closest("[class*='MuiBox-root']");
    let box = layer;
    while (box && getComputedStyle(box).overflow !== "auto") {
      box = box.parentElement;
    }
    expect(box?.scrollTop).toBe(0);
  });
});

describe("Connect a new agent from the SaaS sandbox", () => {
  it("opens every way to connect, for any type of agent", () => {
    openSandbox();
    click("Connect a new agent");
    expect(within(header()).getByText("Connect a new agent")).toBeVisible();
    expect(
      within(header()).getByText(
        "It joins Agents, and this environment is built for it.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Code 22/ })).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: /Hosted platforms 9/ }),
    ).toBeVisible();
    expect(screen.getByRole("tab", { name: /Other ways 7/ })).toBeVisible();
    // The MCP world is a start, not a way to connect an agent.
    expect(
      screen.queryByRole("button", { name: "Use them as an agent's world" }),
    ).toBeNull();
  });

  it("goes back to the sandbox as it was left", () => {
    openSandbox();
    fireEvent.click(screen.getByRole("checkbox", { name: "Slack" }));
    click("Connect a new agent");
    expect(screen.queryByRole("checkbox", { name: "Slack" })).toBeNull();
    click("Back to SaaS sandbox");
    expect(screen.getByRole("checkbox", { name: "Slack" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("connects through Vapi's form, then picks the sample agent", () => {
    openSandbox();
    fireEvent.click(screen.getByRole("checkbox", { name: "Slack" }));
    click("Connect a new agent");
    fireEvent.click(screen.getByRole("tab", { name: /Hosted platforms/ }));
    click("Connect Vapi");

    expect(within(header()).getByText("Vapi")).toBeInTheDocument();
    expect(within(header()).getByText("UI only")).toBeInTheDocument();
    expect(backButton()).toHaveAccessibleName("Back to Connect a new agent");
    expect(
      screen.getByText(/Connecting an agent here isn't wired to the API yet/),
    ).toBeInTheDocument();
    expect(screen.queryByText("Scenarios to generate")).toBeNull();
    expect(screen.queryByText("Parallel worlds")).toBeNull();
    const connect = button("Connect agent");
    expect(connect).toBeDisabled();
    expect(screen.getByText("Add the key")).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("Paste the key"), {
      target: { value: "vk-1" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Assistant ID" }), {
      target: { value: "asst_1" },
    });
    // Preflight is optional, and only a sample here.
    expect(connect).toBeEnabled();
    click("Run preflight");
    fireEvent.click(connect);

    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "UI only: Vapi agent was added as a sample agent for this setup.",
      { variant: "info" },
    );
    expect(within(header()).getByText("SaaS sandbox")).toBeInTheDocument();
    const agent = screen.getByRole("radio", { name: "Vapi agent" });
    expect(agent).toHaveAttribute("aria-checked", "true");
    expect(within(agent).getByText("Sample")).toBeInTheDocument();
    const list = screen.getByRole("radiogroup", { name: "Agent" });
    expect(within(list).getAllByRole("radio")[0]).toBe(agent);
    // The sandbox kept its apps, and the new agent completes it.
    expect(screen.getByRole("checkbox", { name: "Slack" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(button("Set up 1 sandbox")).toBeEnabled();
    expectNoBuildApi();
  });

  it("drops the sample agents when the drawer closes", () => {
    const onClose = render();
    click("SaaS sandbox");
    click("Connect a new agent");
    fireEvent.click(screen.getByRole("tab", { name: /Other ways/ }));
    click("Connect Phone number");
    fireEvent.change(screen.getByRole("textbox", { name: "Number to call" }), {
      target: { value: "4155550142" },
    });
    fireEvent.change(
      screen.getByRole("textbox", { name: "Its instructions" }),
      {
        target: { value: "You book tables." },
      },
    );
    click("Connect agent");
    expect(
      screen.getByRole("radio", { name: "Phone number agent" }),
    ).toBeInTheDocument();
    click("Close");
    expect(onClose).toHaveBeenCalled();
    click("SaaS sandbox");
    expect(
      screen.queryByRole("radio", { name: "Phone number agent" }),
    ).toBeNull();
    expectNoBuildApi();
  });
});

describe("Connect a new agent from a world", () => {
  it("lists only the ways to connect a voice agent for a voice world", async () => {
    await openWorldWithYourAgent("Customer Support Line");
    click("Connect a new agent");
    expect(within(header()).getByText("Connect a voice agent")).toBeVisible();
    expect(
      within(header()).getByText(
        "This world is for voice agents, so only ways to connect a voice agent are shown. It joins Agents, and this environment is built for it.",
      ),
    ).toBeInTheDocument();
    expect(way("GitHub")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /Hosted platforms 7/ }));
    expect(way("Vapi")).toBeInTheDocument();
    expect(way("Telnyx")).toBeInTheDocument();
    expect(way("Salesforce Agentforce")).toBeNull();
    expect(way("Microsoft Copilot Studio")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: /Other ways 6/ }));
    expect(way("Phone number")).toBeInTheDocument();
    expect(way("Intercom Fin")).toBeNull();

    const search = screen.getByRole("textbox", {
      name: "Search ways to connect",
    });
    fireEvent.change(search, { target: { value: "salesforce" } });
    expect(screen.getByText(/No way to connect matches/)).toBeInTheDocument();
  });

  it("lists only the ways to connect a chat agent for a chat world", async () => {
    await openWorldWithYourAgent("Retail Banking Assistant");
    click("Connect a new agent");
    expect(within(header()).getByText("Connect a chat agent")).toBeVisible();
    expect(way("GitHub")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /Hosted platforms 8/ }));
    expect(way("Salesforce Agentforce")).toBeInTheDocument();
    expect(way("Telnyx")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: /Other ways 5/ }));
    expect(way("Agent endpoint")).toBeInTheDocument();
    expect(way("LiveKit project")).toBeNull();
    expect(way("Phone number")).toBeNull();
  });

  it("opens a platform on Chat when connecting for a chat world", async () => {
    await openWorldWithYourAgent("Retail Banking Assistant");
    click("Connect a new agent");
    fireEvent.click(screen.getByRole("tab", { name: /Hosted platforms/ }));
    click("Connect Retell AI");
    expect(screen.getByRole("button", { name: /^Chat/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    click("Back to Connect a chat agent");
    click("Connect ElevenLabs");
    expect(screen.getByText("Chat channel")).toBeInTheDocument();
  });

  it("connects a voice agent and picks it for the world", async () => {
    await openWorldWithYourAgent("Customer Support Line");
    expect(button("Build environment")).toBeDisabled();
    click("Connect a new agent");
    fireEvent.click(screen.getByRole("tab", { name: /Hosted platforms/ }));
    click("Connect Vapi");
    expect(backButton()).toHaveAccessibleName("Back to Connect a voice agent");
    click("Back to Connect a voice agent");
    expect(
      screen.getByRole("tab", { name: /Hosted platforms/ }),
    ).toHaveAttribute("aria-selected", "true");
    // The form slides out before it leaves the page.
    await waitFor(() =>
      expect(screen.queryByPlaceholderText("Paste the key")).toBeNull(),
    );
    click("Connect Vapi");
    fireEvent.change(screen.getByPlaceholderText("Paste the key"), {
      target: { value: "vk-1" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Assistant ID" }), {
      target: { value: "asst_1" },
    });
    click("Connect agent");

    const agent = screen.getByRole("radio", { name: "Vapi agent" });
    expect(agent).toHaveAttribute("aria-checked", "true");
    expect(
      screen.getByRole("radio", { name: "Use it with your agent" }),
    ).toHaveAttribute("aria-checked", "true");
    expect(button("Build environment")).toBeEnabled();
    // The picker and the world's summary both name it.
    expect(screen.getAllByText("Vapi agent")).toHaveLength(2);
    expectNoBuildApi();
  });

  it("runs a live form's preflight on sample checks only", async () => {
    await openWorldWithYourAgent("Customer Support Line");
    click("Connect a new agent");
    click("Connect GitHub");
    fireEvent.change(screen.getByRole("textbox", { name: "Repository link" }), {
      target: { value: "acme/agent" },
    });
    click("Run preflight");
    await waitFor(
      () => expect(screen.getByText("Ready to connect")).toBeInTheDocument(),
      { timeout: 3000 },
    );
    expectNoBuildApi();
  });
});

describe("AgentPicker outside the drawer", () => {
  it("only says connecting from here is UI only", () => {
    baseRender(<AgentPicker agents={[]} value={null} onChange={vi.fn()} />);
    click("Connect a new agent");
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "UI only: connecting a new agent from here isn't wired to the API yet.",
      { variant: "info" },
    );
  });
});
