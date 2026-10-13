import { describe, expect, it, vi } from "vitest";
import { act, render, screen, userEvent, waitFor } from "src/utils/test-utils";
import AgentDraftAssistant from "./AgentDraftAssistant";
import { AgentControlPanel } from "./OmegaSquadPage";

const blank = {
  name: "",
  role: "",
  purpose: "",
  responsibilities: "",
  workMode: "on_demand",
  examples: [],
};
const proposal = {
  name: "Support analyst",
  role: "Analyst",
  purpose: "Summarize customer issues",
  responsibilities: ["Find recurring issues", "Cite evidence"],
  workMode: "on_demand",
  connectorBindingIds: [],
};
const result = (definition = proposal) => ({
  available: true,
  schemaVersion: "omega-workforce-agent-draft/v1",
  proposal: definition,
  reviewRequired: true,
  model: { provider: "fixture", model: "fixture-model", providerId: "fixture" },
  usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2, estimated: false },
  spend: { usd: 0 },
});
const authored = {
  name: proposal.name,
  role: proposal.role,
  purpose: proposal.purpose,
  responsibilities: proposal.responsibilities,
};
const editedDraft = {
  ...blank,
  ...authored,
  responsibilities: proposal.responsibilities.join("\n"),
};
const typePrompt = async (
  text = "Build an agent to summarize support issues",
) =>
  userEvent.type(
    screen.getByRole("textbox", { name: "What should this agent do?" }),
    text,
  );

async function generate() {
  await userEvent.click(
    screen.getByRole("button", { name: "Generate proposal" }),
  );
  await screen.findByRole("region", { name: "Draft assistant proposal" });
}

describe("conversational agent draft assistant", () => {
  it("reviews and refines authored proposals before applying anything", async () => {
    const onApply = vi.fn();
    const client = { draftWorkforceAgent: vi.fn().mockResolvedValue(result()) };
    render(
      <AgentDraftAssistant client={client} draft={blank} onApply={onApply} />,
    );
    await typePrompt();
    await generate();
    expect(client.draftWorkforceAgent).toHaveBeenCalledWith(
      { message: "Build an agent to summarize support issues" },
      expect.stringMatching(/^agent-draft-/),
    );
    expect(onApply).not.toHaveBeenCalled();
    await userEvent.clear(
      screen.getByRole("textbox", { name: "What should this agent do?" }),
    );
    await typePrompt("Make the summary weekly");
    await userEvent.click(
      screen.getByRole("button", { name: "Refine proposal" }),
    );
    await waitFor(() =>
      expect(client.draftWorkforceAgent).toHaveBeenCalledTimes(2),
    );
    expect(client.draftWorkforceAgent.mock.calls[1][0]).toEqual({
      message: "Make the summary weekly",
      currentProposal: authored,
    });
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Apply proposal" }),
      ).toBeEnabled(),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Apply proposal" }),
    );
    expect(onApply).toHaveBeenCalledWith({
      ...authored,
      responsibilities: proposal.responsibilities.join("\n"),
      sourceResponsibilities: proposal.responsibilities,
      workMode: "on_demand",
      assistantProposal: true,
    });
  });

  it("keeps environments, permissions, and existing examples out of replacement changes", async () => {
    const examples = [
      { prompt: "Customer says hello", response: "Acknowledge the customer" },
    ];
    const draft = {
      ...editedDraft,
      examples,
      environmentId: "private-environment",
      environmentRevision: 9,
      connectorBindingIds: ["private-connection"],
      accountId: "private-account",
    };
    const onApply = vi.fn();
    const client = {
      draftWorkforceAgent: vi.fn().mockResolvedValue(
        result({
          ...proposal,
          examples: [{ prompt: "New prompt", response: "New response" }],
        }),
      ),
    };
    render(
      <AgentDraftAssistant client={client} draft={draft} onApply={onApply} />,
    );
    await typePrompt();
    await generate();
    expect(client.draftWorkforceAgent.mock.calls[0][0].currentProposal).toEqual(
      { ...authored, examples },
    );
    expect(
      JSON.stringify(client.draftWorkforceAgent.mock.calls[0][0]),
    ).not.toMatch(/private-|environment|connectorBinding|accountId|workMode/);
    await userEvent.click(
      screen.getByRole("button", { name: "Apply proposal" }),
    );
    expect(onApply.mock.calls[0][0]).not.toHaveProperty("examples");
    expect(onApply.mock.calls[0][0]).not.toHaveProperty("environmentId");
    expect(onApply.mock.calls[0][0]).not.toHaveProperty("connectorBindingIds");
  });

  it("retains a failed prompt and uses the same idempotency key for retry", async () => {
    const client = {
      draftWorkforceAgent: vi
        .fn()
        .mockRejectedValueOnce(new Error("Model unavailable"))
        .mockResolvedValueOnce(result()),
    };
    render(
      <AgentDraftAssistant client={client} draft={blank} onApply={vi.fn()} />,
    );
    await typePrompt();
    await userEvent.click(
      screen.getByRole("button", { name: "Generate proposal" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Model unavailable",
    );
    expect(
      screen.getByRole("textbox", { name: "What should this agent do?" }),
    ).toHaveValue("Build an agent to summarize support issues");
    await generate();
    expect(client.draftWorkforceAgent.mock.calls[1][1]).toBe(
      client.draftWorkforceAgent.mock.calls[0][1],
    );
  });

  it.each([
    { ...proposal, workMode: "always_on" },
    { ...proposal, connectorBindingIds: ["private-binding"] },
    { ...proposal, environmentId: "private-environment" },
    { ...proposal, name: "" },
    { ...proposal, purpose: "学".repeat(1334) },
    {
      ...proposal,
      responsibilities: Array.from({ length: 9 }, () => "x".repeat(950)),
    },
    {
      ...proposal,
      examples: [{ prompt: "p", response: "r", credentials: "secret" }],
    },
  ])(
    "rejects malformed or authority-bearing model proposals %#",
    async (candidate) => {
      const onApply = vi.fn();
      const client = {
        draftWorkforceAgent: vi.fn().mockResolvedValue(result(candidate)),
      };
      render(
        <AgentDraftAssistant client={client} draft={blank} onApply={onApply} />,
      );
      await typePrompt();
      await userEvent.click(
        screen.getByRole("button", { name: "Generate proposal" }),
      );
      expect(await screen.findByRole("alert")).toHaveTextContent(/returned/);
      expect(
        screen.queryByRole("button", { name: "Apply proposal" }),
      ).not.toBeInTheDocument();
      expect(onApply).not.toHaveBeenCalled();
    },
  );

  it("does not submit an incomplete current draft or oversized UTF-8 prompt", async () => {
    const client = { draftWorkforceAgent: vi.fn().mockResolvedValue(result()) };
    const { rerender } = render(
      <AgentDraftAssistant
        client={client}
        draft={{ ...blank, name: "Partial" }}
        onApply={vi.fn()}
      />,
    );
    await typePrompt();
    await generate();
    expect(client.draftWorkforceAgent.mock.calls[0][0]).not.toHaveProperty(
      "currentProposal",
    );
    rerender(
      <AgentDraftAssistant
        key="fresh"
        client={client}
        draft={blank}
        onApply={vi.fn()}
      />,
    );
    await userEvent.click(
      screen.getByRole("textbox", { name: "What should this agent do?" }),
    );
    await userEvent.paste("学".repeat(1334));
    await userEvent.click(
      screen.getByRole("button", { name: "Generate proposal" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("4,000 bytes");
    expect(client.draftWorkforceAgent).toHaveBeenCalledTimes(1);
  });

  it("drops a late response after an authored edit or host client change", async () => {
    let resolve;
    const onApply = vi.fn();
    const client = {
      draftWorkforceAgent: vi.fn(
        () =>
          new Promise((done) => {
            resolve = done;
          }),
      ),
    };
    const { rerender } = render(
      <AgentDraftAssistant client={client} draft={blank} onApply={onApply} />,
    );
    await typePrompt();
    await userEvent.click(
      screen.getByRole("button", { name: "Generate proposal" }),
    );
    rerender(
      <AgentDraftAssistant
        client={client}
        draft={{ ...blank, name: "My newer name" }}
        onApply={onApply}
      />,
    );
    await act(async () => resolve(result()));
    expect(await screen.findByRole("alert")).toHaveTextContent("draft changed");
    expect(
      screen.queryByRole("region", { name: "Draft assistant proposal" }),
    ).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Generate proposal" }),
    );
    rerender(
      <AgentDraftAssistant
        client={{ draftWorkforceAgent: vi.fn() }}
        draft={blank}
        onApply={onApply}
      />,
    );
    await act(async () => resolve(result()));
    expect(
      screen.queryByRole("region", { name: "Draft assistant proposal" }),
    ).not.toBeInTheDocument();
    expect(onApply).not.toHaveBeenCalled();
  });

  it("refuses applying a proposal after user edits or permission loss", async () => {
    const onApply = vi.fn();
    const client = { draftWorkforceAgent: vi.fn().mockResolvedValue(result()) };
    const { rerender } = render(
      <AgentDraftAssistant client={client} draft={blank} onApply={onApply} />,
    );
    await typePrompt();
    await generate();
    rerender(
      <AgentDraftAssistant
        client={client}
        draft={{ ...blank, workMode: "always_on" }}
        onApply={onApply}
      />,
    );
    expect(
      screen.getByRole("button", { name: "Apply proposal" }),
    ).toBeDisabled();
    rerender(
      <AgentDraftAssistant
        client={client}
        draft={blank}
        onApply={onApply}
        disabled
      />,
    );
    expect(
      screen.queryByRole("button", { name: "Apply proposal" }),
    ).not.toBeInTheDocument();
    expect(onApply).not.toHaveBeenCalled();
  });

  it("uses the existing editor review and explicit Create action without auto-saving or deploying", async () => {
    const onCreate = vi.fn().mockResolvedValue({ available: true });
    const onControl = vi.fn();
    const client = {
      draftWorkforceAgent: vi.fn().mockResolvedValue(
        result({
          ...proposal,
          examples: [{ prompt: "Why?", response: "Evidence." }],
        }),
      ),
    };
    render(
      <AgentControlPanel
        agent={null}
        detail={null}
        capabilities={{ canCreateAgent: true }}
        detailLoading={false}
        onCreate={onCreate}
        onUpdate={vi.fn()}
        onControl={onControl}
        client={client}
      />,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Create agent", exact: true }),
    );
    await typePrompt();
    await generate();
    expect(onCreate).not.toHaveBeenCalled();
    await userEvent.click(
      screen.getByRole("button", { name: "Apply proposal" }),
    );
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue(
      proposal.name,
    );
    expect(onCreate).not.toHaveBeenCalled();
    await userEvent.clear(screen.getByRole("textbox", { name: "Name" }));
    await userEvent.type(
      screen.getByRole("textbox", { name: "Name" }),
      "My support analyst",
    );
    await userEvent.click(screen.getByRole("button", { name: "Review agent" }));
    expect(screen.getByText("Response: Evidence.")).toBeInTheDocument();
    expect(onCreate).not.toHaveBeenCalled();
    await userEvent.click(
      screen
        .getAllByRole("button", { name: "Create agent", exact: true })
        .at(-1),
    );
    await waitFor(() =>
      expect(onCreate).toHaveBeenCalledWith({
        ...proposal,
        name: "My support analyst",
        examples: [{ prompt: "Why?", response: "Evidence." }],
      }),
    );
    expect(onControl).not.toHaveBeenCalled();
  });
});
