import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";

const navigate = vi.fn();
const createHarnessAdaptation = vi.fn();
const releases = { data: [], error: null, isLoading: false };

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
  storeHarnessSecretValues: async () => ({
    secret_refs: {
      RETELL_API_KEY: { manager: "platform-vault", key: "ref-1" },
    },
  }),
}));

vi.mock("src/components/run-tests/common", () => ({
  useAgentDefinitions: () => ({
    agentDefinitions: [{ id: "agent-1", agent_name: "Clinic chat" }],
    isLoading: false,
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
    await user.type(within(dialog).getByLabelText("RETELL_API_KEY"), "secret");
    await user.type(
      within(dialog).getByLabelText("Retell chat agent ID"),
      "agent_abc",
    );
    await user.click(
      within(dialog).getByRole("button", { name: /Adapt to my agent/ }),
    );

    expect(createHarnessAdaptation).toHaveBeenCalledWith(
      expect.objectContaining({
        suite_id: "suite-1",
        agent_version_id: "version-1",
        agent: expect.objectContaining({
          connector: "retell_chat",
          config: { agent_id: "agent_abc" },
        }),
      }),
    );
    expect(navigate).toHaveBeenCalledWith(expect.stringContaining("job-1"));
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
