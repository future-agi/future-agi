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
  Use with my agent over the fit request itself: the table and what is sent are always the
  pairing on screen, for the agent on screen.
*/

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  endpoints: {},
}));

vi.mock("src/api/agents/connectedAgents", () => ({
  useConnectedAgents: () => ({
    data: {
      items: [
        { id: "a1", name: "ShopMate", modality: "text", source: "retell" },
        { id: "a2", name: "Returns", modality: "text", source: "retell" },
        { id: "a3", name: "Unread", modality: "text", source: "retell" },
      ],
      total: 3,
    },
    isLoading: false,
  }),
}));

const refetchVersions = vi.fn();
vi.mock("src/api/agent-definition/agent-definition-version", () => ({
  useAgentDefinitionVersions: ({ selectedAgentId }) =>
    selectedAgentId === "a3"
      ? {
          data: undefined,
          isLoading: false,
          error: { message: "versions are down" },
          refetch: refetchVersions,
        }
      : {
          data: selectedAgentId
            ? {
                pages: [
                  {
                    results: [
                      { id: `version-${selectedAgentId}`, is_active: true },
                    ],
                  },
                ],
              }
            : undefined,
          isLoading: false,
          error: null,
        },
}));

vi.mock("src/api/harness/harness", () => ({
  harnessIdempotencyKey: () => "idem-test",
}));

vi.mock("src/api/simulate-environments/environment", () => ({
  harnessJobQuery: (id) => ({
    queryKey: ["harness-job", id],
    queryFn: async () => ({}),
  }),
}));

const { default: axios } = await import("src/utils/axios");
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
  tools: [],
  policy: { parameters: [], agent_rules: [] },
  suites: [
    { id: "suite-1", name: "retail_base", scenario_count: 3 },
    { id: "suite-2", name: "retail_edge", scenario_count: 5 },
  ],
  catalogue: null,
});

const fitOf = (agent, tools, pairing, unmeasured = []) => ({
  agent: { name: agent, version: "v1" },
  agent_tools: tools.map((name) => ({ name })),
  rows: Object.entries(pairing).map(([worldTool, agentTool]) => ({
    world: worldTool,
    agent: agentTool,
    kind: agentTool ? "mapped" : "missing",
  })),
  platform_actions: [],
  total: 3,
  unmeasured,
});

// Fit requests wait here until a test answers them.
let asked = [];
const answer = (body) => asked.shift().resolve({ data: body });
const refuse = (error) => asked.shift().reject(error);
const SUGGESTED = { get_order: "order_details", cancel: null, refund: null };
const TOOLS = ["order_details", "cancel_it", "refund_it"];

const render = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  baseRender(
    <QueryClientProvider client={client}>
      <ReadyEnvironmentPanel item={{ world, initialTab: "start" }} />
    </QueryClientProvider>,
  );
};
const pick = (name) => fireEvent.click(screen.getByRole("radio", { name }));
const select = (worldTool, agent = "ShopMate") =>
  screen.getByRole("combobox", { name: `${agent}'s tool for ${worldTool}` });
const pair = (worldTool, agentTool) => {
  fireEvent.mouseDown(select(worldTool));
  fireEvent.click(within(screen.getByRole("listbox")).getByText(agentTool));
};
const adapt = () => screen.getByRole("button", { name: "Adapt to my agent" });
const run = () => screen.getByRole("button", { name: /^Run the/ });
const submitted = () =>
  axios.post.mock.calls.find(([url]) => url.includes("adaptations"))?.[1];
const shopMateFit = async () => {
  render();
  pick("ShopMate");
  await waitFor(() => expect(asked).toHaveLength(1));
  answer(fitOf("ShopMate", TOOLS, SUGGESTED));
  await waitFor(() => expect(adapt()).toBeEnabled());
};

beforeEach(() => {
  asked = [];
  navigate.mockReset();
  refetchVersions.mockReset();
  axios.post.mockReset();
  axios.post.mockImplementation((url, body) =>
    url.includes("/fit/")
      ? new Promise((resolve, reject) => {
          asked.push({ body, resolve, reject });
        })
      : Promise.resolve({ data: { job: { job_id: "adapt-job" } } }),
  );
});

describe("the fit on screen", () => {
  it("never shows or sends one agent's pairing for another", async () => {
    await shopMateFit();

    pick("Returns");

    await waitFor(() => expect(asked).toHaveLength(1));
    expect(asked[0].body.agent_version_id).toBe("version-a2");
    expect(asked[0].body).not.toHaveProperty("pairing");
    expect(screen.queryByText("World tool")).toBeNull();
    expect(
      screen.getByRole("progressbar", { name: "Reading your agent's tools" }),
    ).toBeInTheDocument();
    expect(adapt()).toBeDisabled();
    expect(run()).toBeDisabled();
  });

  it("starts the fit over for another suite", async () => {
    await shopMateFit();

    fireEvent.click(screen.getByRole("button", { name: "Change" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /retail_edge/ }));

    await waitFor(() => expect(asked).toHaveLength(1));
    expect(asked[0].body.suite_id).toBe("suite-2");
    expect(screen.queryByText("World tool")).toBeNull();
    expect(adapt()).toBeDisabled();
  });

  it("shows an edit at once and sends it once it has been counted", async () => {
    await shopMateFit();

    pair("cancel", "cancel_it");

    expect(screen.getByText(/haven't been counted yet/)).toBeInTheDocument();
    expect(adapt()).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "2 matched" }));
    expect(select("cancel")).toHaveTextContent("cancel_it");

    await waitFor(() => expect(asked).toHaveLength(1));
    const edited = { ...SUGGESTED, cancel: "cancel_it" };
    expect(asked[0].body.pairing).toEqual(edited);
    answer(fitOf("ShopMate", TOOLS, edited));
    await waitFor(() => expect(adapt()).toBeEnabled());
    fireEvent.click(adapt());
    await waitFor(() => expect(submitted()).toBeTruthy());
    expect(submitted().pairing).toEqual(edited);
  });

  it("keeps both of two quick edits", async () => {
    await shopMateFit();

    pair("cancel", "cancel_it");
    pair("refund", "refund_it");

    const both = { ...SUGGESTED, cancel: "cancel_it", refund: "refund_it" };
    await waitFor(() =>
      expect(asked[asked.length - 1]?.body.pairing).toEqual(both),
    );
    asked = [asked[asked.length - 1]];
    answer(fitOf("ShopMate", TOOLS, both));
    await waitFor(() => expect(run()).toBeEnabled());
    fireEvent.click(run());
    await waitFor(() => expect(submitted()).toBeTruthy());
    expect(submitted().pairing).toEqual(both);
  });

  it("keeps the table when a pairing can't be counted, with a retry", async () => {
    await shopMateFit();

    pair("cancel", "cancel_it");
    await waitFor(() => expect(asked).toHaveLength(1));
    refuse({ message: "the fit timed out", statusCode: 504 });

    expect(await screen.findByText("the fit timed out")).toBeInTheDocument();
    expect(screen.getByText("World tool")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "2 matched" }),
    ).toBeInTheDocument();
    expect(adapt()).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(asked).toHaveLength(1));
    answer(fitOf("ShopMate", TOOLS, { ...SUGGESTED, cancel: "cancel_it" }));
    await waitFor(() => expect(adapt()).toBeEnabled());
    expect(screen.queryByText("the fit timed out")).toBeNull();
  });

  it("says nothing can be measured by the cases counted, not the tools paired", async () => {
    render();
    pick("ShopMate");
    await waitFor(() => expect(asked).toHaveLength(1));
    answer(
      fitOf(
        "ShopMate",
        TOOLS,
        SUGGESTED,
        ["one", "two", "three"].map((key) => ({ key, title: key })),
      ),
    );

    expect(await screen.findByText("Can't be measured")).toBeInTheDocument();
    expect(
      screen.getByText(/no case has every tool it needs paired/),
    ).toBeInTheDocument();
    expect(run()).toBeDisabled();
    expect(adapt()).toBeEnabled();
  });

  it("says the agent's versions couldn't be read rather than that it has none", () => {
    render();
    pick("Unread");

    expect(
      screen.getByText("Couldn't read the agent's versions: versions are down"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/has no version/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetchVersions).toHaveBeenCalled();
  });
});
