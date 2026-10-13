import { describe, expect, it, vi } from "vitest";
import {
  render,
  screen,
  userEvent,
  waitFor,
  within,
  act,
} from "src/utils/test-utils";
import { AgentControlPanel } from "./OmegaSquadPage";
import {
  exportAgentDefinition,
  importAgentDefinition,
  importedDefinitionDraft,
} from "./agentDefinition";

const modelCatalog = {
  available: true,
  schemaVersion: "omega-workforce/v1",
  defaultModelId: "model-a",
  models: [
    { modelId: "model-a", selectable: true, billing: "omega_served" },
    {
      modelId: "model-b",
      selectable: true,
      billing: "omega_served",
      rate: {
        inputPer1MUsd: 2,
        outputPer1MUsd: 8,
        cachedInputPer1MUsd: 0.2,
        cacheWritePer1MUsd: 2.5,
        rateCardVersion: "2026-10-13",
      },
    },
    {
      modelId: "model-private",
      selectable: false,
      reason: "Not available on this plan",
    },
  ],
};
const agent = {
  id: "agent-a",
  name: "Analyst",
  version: 2,
  role: "Analyst",
  purpose: "Write a brief",
  responsibilities: [],
  workMode: "on_demand",
  modelId: "model-a",
  deployedModelId: "model-a",
  examples: [],
  status: "idle",
};
function props(overrides = {}) {
  return {
    agent: null,
    detail: null,
    capabilities: {
      canCreateAgent: true,
      permittedActions: ["create", "update"],
    },
    detailLoading: false,
    onCreate: vi.fn().mockResolvedValue({ available: true }),
    onUpdate: vi.fn().mockResolvedValue({ available: true }),
    onControl: vi.fn(),
    client: { listWorkforceModels: vi.fn().mockResolvedValue(modelCatalog) },
    ...overrides,
  };
}
async function start() {
  await userEvent.click(screen.getByRole("button", { name: "Create agent" }));
  await userEvent.click(
    screen.getByRole("button", { name: "Research analyst" }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("combobox", { name: "Agent model" }),
    ).not.toBeDisabled(),
  );
}
async function create() {
  await userEvent.click(screen.getByRole("button", { name: "Review agent" }));
  await userEvent.click(
    within(
      screen.getByRole("region", { name: "Create a managed agent" }),
    ).getByRole("button", { name: "Create agent" }),
  );
}

describe("Customer per-agent model selection", () => {
  it("does not display an old agent's revision history after switching selection", async () => {
    let late;
    const second = { ...agent, id: "agent-b", name: "Writer" };
    const history = (current, name) => ({
      available: true,
      agent: current,
      currentVersion: 1,
      revisions: [{ version: 1, name, current: true }],
      deployments: [],
    });
    const input = props({
      agent,
      detail: { available: true, agent },
      client: {
        listWorkforceModels: vi.fn().mockResolvedValue(modelCatalog),
        listWorkforceAgentRevisions: vi.fn((id) =>
          id === agent.id
            ? new Promise((resolve) => {
                late = resolve;
              })
            : Promise.resolve(history(second, "Current writer revision")),
        ),
        listWorkforceAgentDeployments: vi
          .fn()
          .mockResolvedValue({ available: true, deployments: [] }),
      },
    });
    const view = render(<AgentControlPanel {...input} />);
    await userEvent.click(
      screen.getByRole("button", { name: "Inspect history" }),
    );
    view.rerender(
      <AgentControlPanel
        {...input}
        agent={second}
        detail={{ available: true, agent: second }}
      />,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Inspect history" }),
    );
    expect(
      await screen.findByText("Current writer revision"),
    ).toBeInTheDocument();
    await act(async () => late(history(agent, "Previous analyst revision")));
    expect(screen.getByText("Current writer revision")).toBeInTheDocument();
    expect(
      screen.queryByText("Previous analyst revision"),
    ).not.toBeInTheDocument();
  });

  it("shows permitted choices and public rates, then creates with the reviewed model", async () => {
    const input = props();
    render(<AgentControlPanel {...input} />);
    await start();
    expect(
      screen.getByRole("option", { name: /model-private/ }),
    ).toBeDisabled();
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Agent model" }),
      "model-b",
    );
    expect(
      screen.getByText(/Published token rate: \$2 input \/ \$8 output/),
    ).toBeInTheDocument();
    await create();
    expect(input.onCreate).toHaveBeenCalledWith(
      expect.objectContaining({ modelId: "model-b" }),
    );
    expect(input.onControl).not.toHaveBeenCalled();
  });

  it("preserves the existing model on unrelated edits and saves a change as a versioned definition only", async () => {
    const input = props({ agent, detail: { available: true, agent } });
    const view = render(<AgentControlPanel {...input} />);
    expect(
      screen.getByText(
        /Saved model: model-a. Active deployment model: model-a/,
      ),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await userEvent.click(screen.getByRole("button", { name: "Save agent" }));
    expect(input.onUpdate).toHaveBeenLastCalledWith(
      "agent-a",
      expect.not.objectContaining({ modelId: expect.anything() }),
    );
    view.rerender(<AgentControlPanel {...input} />);
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await waitFor(() =>
      expect(
        screen.getByRole("combobox", { name: "Agent model" }),
      ).not.toBeDisabled(),
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Agent model" }),
      "model-b",
    );
    await userEvent.click(screen.getByRole("button", { name: "Save agent" }));
    expect(input.onUpdate).toHaveBeenLastCalledWith(
      "agent-a",
      expect.objectContaining({ expectedVersion: 2, modelId: "model-b" }),
    );
    expect(input.onControl).not.toHaveBeenCalled();
  });

  it("keeps an explicit selection after a server refusal and does not retry or deploy", async () => {
    const input = props({
      onCreate: vi.fn().mockResolvedValue({
        available: false,
        reason: "Model no longer available",
      }),
    });
    render(<AgentControlPanel {...input} />);
    await start();
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Agent model" }),
      "model-b",
    );
    await create();
    expect(screen.getByText("Model no longer available")).toBeInTheDocument();
    expect(screen.getByText("Agent model: model-b.")).toBeInTheDocument();
    expect(input.onCreate).toHaveBeenCalledTimes(1);
    expect(input.onControl).not.toHaveBeenCalled();
  });

  it("does not authorize an explicit choice from a stale workspace response", async () => {
    let late;
    const input = props();
    const view = render(<AgentControlPanel {...input} />);
    await start();
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Agent model" }),
      "model-b",
    );
    const nextClient = {
      listWorkforceModels: vi.fn().mockImplementation(
        () =>
          new Promise((resolve) => {
            late = resolve;
          }),
      ),
    };
    view.rerender(<AgentControlPanel {...input} client={nextClient} />);
    expect(screen.getByRole("button", { name: "Review agent" })).toBeDisabled();
    await waitFor(() =>
      expect(nextClient.listWorkforceModels).toHaveBeenCalled(),
    );
    await act(async () =>
      late({
        ...modelCatalog,
        models: [{ modelId: "model-a", selectable: true }],
      }),
    );
    expect(screen.getByRole("button", { name: "Review agent" })).toBeDisabled();
    expect(input.onCreate).not.toHaveBeenCalled();
  });

  it.each([
    { ...modelCatalog, models: [{ modelId: "model-b", selectable: "true" }] },
    {
      ...modelCatalog,
      models: [
        { modelId: "model-b", selectable: true, reason: { unsafe: true } },
      ],
    },
    {
      ...modelCatalog,
      models: [
        {
          ...modelCatalog.models[1],
          rate: { ...modelCatalog.models[1].rate, caveat: { unsafe: true } },
        },
      ],
    },
    {
      ...modelCatalog,
      models: [
        {
          ...modelCatalog.models[1],
          rate: { ...modelCatalog.models[1].rate, outputPer1MUsd: -1 },
        },
      ],
    },
    { ...modelCatalog, defaultModelId: "" },
  ])(
    "rejects a malformed catalog without changing runtime-default create behavior (%#)",
    async (response) => {
      const input = props({
        client: {
          listWorkforceModels: vi.fn().mockResolvedValue(response),
        },
      });
      render(<AgentControlPanel {...input} />);
      await userEvent.click(
        screen.getByRole("button", { name: "Create agent" }),
      );
      await userEvent.click(
        screen.getByRole("button", { name: "Research analyst" }),
      );
      expect(
        await screen.findByText("The model catalog could not be verified."),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("combobox", { name: "Agent model" }),
      ).toBeDisabled();
      await create();
      expect(input.onCreate).toHaveBeenCalledWith(
        expect.not.objectContaining({ modelId: expect.anything() }),
      );
    },
  );

  it("discards a previous workspace catalog that completes after the new catalog", async () => {
    let oldResponse;
    const input = props({
      client: {
        listWorkforceModels: vi.fn().mockImplementation(
          () =>
            new Promise((resolve) => {
              oldResponse = resolve;
            }),
        ),
      },
    });
    const view = render(<AgentControlPanel {...input} />);
    await userEvent.click(screen.getByRole("button", { name: "Create agent" }));
    await userEvent.click(
      screen.getByRole("button", { name: "Research analyst" }),
    );
    await waitFor(() =>
      expect(input.client.listWorkforceModels).toHaveBeenCalled(),
    );
    const nextClient = {
      listWorkforceModels: vi.fn().mockResolvedValue({
        ...modelCatalog,
        models: [modelCatalog.models[0]],
      }),
    };
    view.rerender(<AgentControlPanel {...input} client={nextClient} />);
    await waitFor(() =>
      expect(
        screen.getByRole("combobox", { name: "Agent model" }),
      ).not.toBeDisabled(),
    );
    await act(async () => oldResponse(modelCatalog));
    expect(
      screen.queryByRole("option", { name: "model-b" }),
    ).not.toBeInTheDocument();
  });

  it("shows included billing without inventing a token rate", async () => {
    const input = props({
      client: {
        listWorkforceModels: vi.fn().mockResolvedValue({
          ...modelCatalog,
          models: [
            { modelId: "model-a", selectable: true, billing: "included" },
          ],
        }),
      },
    });
    render(<AgentControlPanel {...input} />);
    await start();
    expect(
      screen.getByText(
        "Model usage is included under the runtime’s billing policy.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Published token rate/)).not.toBeInTheDocument();
  });

  it("preserves the requested model in portable definitions for destination revalidation", () => {
    const portable = importAgentDefinition(exportAgentDefinition(agent));
    expect(portable.definition.modelId).toBe("model-a");
    expect(importedDefinitionDraft(portable)).toMatchObject({
      modelId: "model-a",
      modelChanged: true,
    });
    expect(() =>
      importAgentDefinition(
        JSON.stringify({
          ...portable,
          schemaVersion: "omega-squad-agent-definition/v1",
          definition: { ...portable.definition, modelId: "" },
        }),
      ),
    ).toThrow();
  });
});
