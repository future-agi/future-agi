import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";

const navigate = vi.fn();
const createHarnessAdaptation = vi.fn();
const releases = {
  data: [],
  error: null,
  isLoading: false,
  refetch: vi.fn(),
};

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

vi.mock("src/api/harness/releases", () => ({
  useHarnessReleases: () => releases,
  createHarnessAdaptation: (...args) => createHarnessAdaptation(...args),
}));

vi.mock("src/api/harness/harness", () => ({
  harnessIdempotencyKey: () => "key-1",
}));

// The list answers for the filters it was asked for.
const AGENTS = [
  { id: "agent-1", name: "Clinic chat", source: "retell", modality: "text" },
  { id: "agent-2", name: "Clinic voice", source: "retell", modality: "voice" },
  { id: "agent-3", name: "Clinic line", source: "vapi", modality: "voice" },
  { id: "agent-4", name: "Repo chat", source: "github", modality: "text" },
];
vi.mock("src/api/agents/connectedAgents", () => ({
  useConnectedAgents: ({ filters }) => ({
    data: {
      items: AGENTS.filter(
        (agent) =>
          filters.source.includes(agent.source) &&
          filters.agent_type.includes(agent.modality),
      ),
    },
    isFetching: false,
    isPlaceholderData: false,
  }),
}));

vi.mock("src/api/agent-definition/agent-definition-version", () => ({
  useAgentDefinitionVersions: () => ({
    data: { pages: [{ results: [{ id: "version-1", version_name: "v1" }] }] },
    isLoading: false,
  }),
}));

const { default: PrebuiltEnvironmentsBrowse } = await import(
  "../PrebuiltEnvironmentsBrowse"
);

const CLINIC = {
  id: "release-1",
  digest: "sha256:" + "a".repeat(64),
  name: "appointment_booking",
  system: true,
  parent: null,
  tools: [],
  policy: { parameters: [], agent_rules: [] },
  suites: [
    {
      id: "suite-1",
      digest: "sha256:" + "b".repeat(64),
      name: "appointment_booking_base",
      scenario_count: 8,
      origins: { base: 8, specialized: 0, custom: 0 },
      policy_profile: {},
    },
  ],
};

const renderBrowse = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <PrebuiltEnvironmentsBrowse />
    </QueryClientProvider>,
  );

describe("PrebuiltEnvironmentsBrowse", () => {
  beforeEach(() => {
    navigate.mockReset();
    createHarnessAdaptation.mockReset();
    releases.data = [CLINIC];
    releases.error = null;
    releases.isLoading = false;
    releases.refetch.mockReset();
  });

  it("lists released environments only, never the static template catalog", () => {
    renderBrowse();

    expect(screen.getByText("appointment_booking")).toBeInTheDocument();
    expect(screen.queryByText("Customer Support Line")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Retail Banking Assistant"),
    ).not.toBeInTheDocument();
    expect(
      screen.getAllByRole("button", { name: /Use with my agent/ }),
    ).toHaveLength(1);
  });

  it("adopts a release only by adapting the agent to its suite", async () => {
    const user = userEvent.setup();
    createHarnessAdaptation.mockResolvedValue({ job: { job_id: "job-1" } });
    renderBrowse();

    await user.click(screen.getByRole("button", { name: /Use with my agent/ }));
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).queryByRole("button", { name: /Compose/ }),
    ).not.toBeInTheDocument();

    await user.click(within(dialog).getByRole("combobox", { name: "Agent" }));
    await user.click(screen.getByRole("option", { name: "Clinic chat" }));
    await user.click(
      within(dialog).getByRole("combobox", { name: "Agent version" }),
    );
    await user.click(screen.getByRole("option", { name: "v1" }));
    // No key or provider id is typed: the server reads them from the version as connected.
    expect(
      within(dialog).queryByLabelText("RETELL_API_KEY"),
    ).not.toBeInTheDocument();
    await user.click(
      within(dialog).getByRole("button", { name: /Adapt to my agent/ }),
    );

    expect(createHarnessAdaptation).toHaveBeenCalledWith(
      expect.objectContaining({
        suite_id: "suite-1",
        agent_version_id: "version-1",
      }),
    );
    expect(createHarnessAdaptation.mock.calls[0][0]).not.toHaveProperty(
      "agent",
    );
    expect(navigate).toHaveBeenCalledWith(
      "/dashboard/simulate/environments/job-1",
    );
  });

  it("offers the agents of the platform picked, and only those it can adapt", async () => {
    const user = userEvent.setup();
    renderBrowse();
    await user.click(screen.getByRole("button", { name: /Use with my agent/ }));
    const dialog = screen.getByRole("dialog");
    const agentsOffered = async () => {
      const field = within(dialog).getByRole("combobox", { name: "Agent" });
      await user.click(field);
      const names = screen.getAllByRole("option").map((o) => o.textContent);
      await user.keyboard("{Escape}");
      return names;
    };

    expect(await agentsOffered()).toEqual(["Clinic chat"]);
    await user.click(within(dialog).getByLabelText("Agent platform"));
    await user.click(screen.getByRole("option", { name: "Vapi" }));
    expect(await agentsOffered()).toEqual(["Clinic line"]);
  });

  it("closes a dialog from its cross", async () => {
    const user = userEvent.setup();
    renderBrowse();
    for (const name of [/Use with my agent/, /Run our agent/]) {
      await user.click(screen.getByRole("button", { name }));
      await user.click(
        within(screen.getByRole("dialog")).getByRole("button", {
          name: "Close",
        }),
      );
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    }
  });

  it("loads, rather than reading as none", () => {
    releases.data = undefined;
    releases.isLoading = true;
    renderBrowse();

    expect(
      screen.getByRole("progressbar", {
        name: "Loading prebuilt environments",
      }),
    ).toBeInTheDocument();
    expect(screen.queryByText("No prebuilt environments yet.")).toBeNull();
  });

  it("says when they can't be read, with a retry, and keeps a list read before", async () => {
    releases.data = undefined;
    releases.error = { message: "releases are down" };
    renderBrowse();

    expect(screen.getByText("releases are down")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(releases.refetch).toHaveBeenCalled();

    releases.data = [CLINIC];
    renderBrowse();
    expect(screen.getByText("appointment_booking")).toBeInTheDocument();
  });

  it("says there is nothing to adopt when no environment is released", () => {
    releases.data = [];
    renderBrowse();

    expect(
      screen.getByText("No prebuilt environments yet."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Use with my agent/ }),
    ).not.toBeInTheDocument();
  });
});
