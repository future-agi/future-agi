import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, userEvent, waitFor } from "src/utils/test-utils";
import { AgentControlPanel } from "./OmegaSquadPage";
import AgentDefinitionActions from "./AgentDefinitionActions";
import { exportAgentDefinition } from "./agentDefinition";

const agent = {
  id: "agent-a",
  name: "Researcher",
  role: "Analyst",
  purpose: "Find evidence",
  responsibilities: ["Explain\nwhy"],
  workMode: "on_demand",
  examples: [{ prompt: "Why?", response: "Evidence." }],
};
const portableFile = () =>
  new File([exportAgentDefinition(agent)], "research.omega-agent.json", {
    type: "application/json",
  });

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("agent definition actions", () => {
  it("imports from an empty roster into the review flow, preserving examples and multiline responsibilities", async () => {
    const create = vi.fn().mockResolvedValue({ available: true });
    render(
      <AgentControlPanel
        agent={null}
        detail={null}
        capabilities={{ canCreateAgent: true }}
        detailLoading={false}
        onCreate={create}
        onUpdate={vi.fn()}
        onControl={vi.fn()}
        client={{}}
      />,
    );
    await userEvent.upload(
      screen.getByLabelText("Agent definition file"),
      portableFile(),
    );
    expect(await screen.findByRole("textbox", { name: "Name" })).toHaveValue(
      "Researcher",
    );
    expect(create).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Review agent" }));
    expect(screen.getByText("Response: Evidence.")).toBeInTheDocument();
    await userEvent.click(
      screen.getAllByRole("button", { name: "Create agent" }).at(-1),
    );
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({
        name: "Researcher",
        role: "Analyst",
        purpose: "Find evidence",
        responsibilities: ["Explain\nwhy"],
        workMode: "on_demand",
        examples: agent.examples,
        connectorBindingIds: [],
      }),
    );
  });
  it("imports a ZIP manifest into the same review flow without creating immediately", async () => {
    const create = vi.fn().mockResolvedValue({ available: true });
    const archive = Uint8Array.from(
      atob(
        "UEsDBBQAAAAIAAJFTV0OFnX04gAAAEEBAAAQAAAAb21lZ2EtYWdlbnQuanNvbkVQPU/DQAz9K5XnBMSaBSFUNpYOMECFTOI0Fne+6/mSNIry3/FVlTr6+X34eQVtB/L4QUk5CDQQPJ2w1vOIXY0nklx31LNwtvXj9AQV3GdoVhD0ZLIDKWEyr2SMFFzBXgTdotmAOKYYtGBvLN2OJu5IWipU0hhE+ZedWZJC8wX7S3TI8i3zsBjllTPtNIyptfWxgjmkv/fQFTd0My76Y5dUQBf00V0dVogp+JiN8Tksz/eYotnfwnc9J80PsB23CpTyGA90HjlRV2qRTJyCeHsAND06pQraIEJtKW4hV2zb/gFQSwECFAAUAAAACAACRU1dDhZ19OIAAABBAQAAEAAAAAAAAAAAAAAAAAAAAAAAb21lZ2EtYWdlbnQuanNvblBLBQYAAAAAAQABAD4AAAAQAQAAAAA=",
      ),
      (char) => char.charCodeAt(0),
    );
    render(
      <AgentControlPanel
        agent={null}
        detail={null}
        capabilities={{ canCreateAgent: true }}
        detailLoading={false}
        onCreate={create}
        onUpdate={vi.fn()}
        onControl={vi.fn()}
        client={{}}
      />,
    );
    await userEvent.upload(
      screen.getByLabelText("Agent definition file"),
      new File([archive], "research.zip", { type: "application/zip" }),
    );
    expect(await screen.findByRole("textbox", { name: "Name" })).toHaveValue(
      "Researcher",
    );
    expect(create).not.toHaveBeenCalled();
  });
  it("duplicates from a fresh server read without reusing connected environment identities", async () => {
    const onDraft = vi.fn();
    const client = {
      getWorkforceAgent: vi
        .fn()
        .mockResolvedValue({
          available: true,
          agent: { ...agent, environment: { id: "env-private", revision: 2 } },
        }),
    };
    render(
      <AgentDefinitionActions
        agentId="agent-a"
        client={client}
        canCreate
        disabled={false}
        onDraft={onDraft}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Duplicate" }));
    await waitFor(() => expect(onDraft).toHaveBeenCalled());
    expect(onDraft.mock.calls[0][0].definition).not.toHaveProperty(
      "environmentId",
    );
    expect(onDraft.mock.calls[0][0].setupRequired.environment).toBe(true);
    expect(onDraft.mock.calls[0][1]).toBe(true);
  });
  it("shows an imported always-on definition truthfully and preserves its selected mode on create", async () => {
    const create = vi.fn().mockResolvedValue({ available: true });
    render(
      <AgentControlPanel
        agent={null}
        detail={null}
        capabilities={{ canCreateAgent: true }}
        detailLoading={false}
        onCreate={create}
        onUpdate={vi.fn()}
        onControl={vi.fn()}
        client={{}}
      />,
    );
    const file = new File(
      [exportAgentDefinition({ ...agent, workMode: "always_on" })],
      "always-on.json",
      { type: "application/json" },
    );
    await userEvent.upload(
      screen.getByLabelText("Agent definition file"),
      file,
    );
    expect(
      await screen.findByRole("combobox", { name: "Work mode" }),
    ).toHaveTextContent("Always on");
    await userEvent.click(screen.getByRole("button", { name: "Review agent" }));
    expect(screen.getByText("Always on")).toBeInTheDocument();
    await userEvent.click(
      screen.getAllByRole("button", { name: "Create agent" }).at(-1),
    );
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({ workMode: "always_on" }),
      ),
    );
  });
  it("downloads only the latest authored definition and uses a safe JSON filename", async () => {
    const onDraft = vi.fn();
    const client = {
      getWorkforceAgent: vi
        .fn()
        .mockResolvedValue({
          available: true,
          agent: {
            ...agent,
            name: "Research / report",
            environment: { id: "private-env" },
            connectorBindings: [{ id: "private-connection" }],
          },
        }),
    };
    const createObjectURL = vi.fn().mockReturnValue("blob:definition-test");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal(
      "URL",
      class extends URL {
        static createObjectURL = createObjectURL;
        static revokeObjectURL = revokeObjectURL;
      },
    );
    let download;
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
      function () {
        download = { href: this.href, filename: this.download };
      },
    );
    render(
      <AgentDefinitionActions
        agentId="agent-a"
        client={client}
        canCreate={false}
        disabled={false}
        onDraft={onDraft}
      />,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Export definition" }),
    );
    await waitFor(() => expect(createObjectURL).toHaveBeenCalledOnce());
    expect(download).toEqual({
      href: "blob:definition-test",
      filename: "Research-report.omega-agent.json",
    });
    expect(createObjectURL.mock.calls[0][0]).toBeInstanceOf(Blob);
    const downloaded = await new Promise((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.readAsText(createObjectURL.mock.calls[0][0]);
    });
    expect(JSON.parse(downloaded).definition).toMatchObject({
      name: "Research / report",
      examples: agent.examples,
    });
    expect(downloaded).not.toMatch(/private-env|private-connection/);
    expect(onDraft).not.toHaveBeenCalled();
    await waitFor(
      () =>
        expect(revokeObjectURL).toHaveBeenCalledWith("blob:definition-test"),
      { timeout: 2000 },
    );
  });
  it("refuses an agent response from a different selection instead of copying it", async () => {
    const onDraft = vi.fn();
    const client = {
      getWorkforceAgent: vi
        .fn()
        .mockResolvedValue({
          available: true,
          agent: { ...agent, id: "other-agent" },
        }),
    };
    render(
      <AgentDefinitionActions
        agentId="agent-a"
        client={client}
        canCreate
        disabled={false}
        onDraft={onDraft}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Duplicate" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "complete agent definition could not be read",
    );
    expect(onDraft).not.toHaveBeenCalled();
  });
  it("rejects late copies when the selected agent or permissions change", async () => {
    let resolve;
    const onDraft = vi.fn();
    const client = {
      getWorkforceAgent: vi.fn(
        () =>
          new Promise((done) => {
            resolve = done;
          }),
      ),
    };
    const { rerender } = render(
      <AgentDefinitionActions
        agentId="agent-a"
        client={client}
        canCreate
        disabled={false}
        onDraft={onDraft}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Duplicate" }));
    rerender(
      <AgentDefinitionActions
        agentId="agent-b"
        client={client}
        canCreate={false}
        disabled={false}
        onDraft={onDraft}
      />,
    );
    await act(async () => resolve({ available: true, agent }));
    expect(onDraft).not.toHaveBeenCalled();
  });
  it("rejects oversized files before a read and keeps malformed imports visible", async () => {
    render(
      <AgentDefinitionActions
        client={{}}
        canCreate
        disabled={false}
        onDraft={vi.fn()}
      />,
    );
    await userEvent.upload(
      screen.getByLabelText("Agent definition file"),
      new File(["x".repeat(262145)], "large.json", {
        type: "application/json",
      }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("256 KiB");
    await userEvent.upload(
      screen.getByLabelText("Agent definition file"),
      new File(['{"schemaVersion":"other"}'], "bad.json", {
        type: "application/json",
      }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /missing or unsupported/,
    );
  });
});
