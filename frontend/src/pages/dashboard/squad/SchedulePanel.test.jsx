import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import { SchedulePanel } from "./OmegaSquadPage";

const baseProps = {
  result: { available: true, schedules: [] },
  agents: [{ id: "agent-a", name: "Research agent", status: "idle" }],
  client: { listWorkforce: vi.fn(), listWorkforceAgentDeployments: vi.fn() },
  busyId: "",
  selectedId: "",
  detail: null,
  detailLoading: false,
  onSelect: vi.fn(),
  onRun: vi.fn(),
  onCreate: vi
    .fn()
    .mockResolvedValue({ available: true, schedule: { id: "sched-1" } }),
  onUpdate: vi.fn().mockResolvedValue({ available: true }),
  onDelete: vi.fn(),
};

describe("Omega Squad schedule agent editor", () => {
  beforeEach(() => vi.clearAllMocks());
  it("creates a time schedule with a selected current-deployment agent", async () => {
    const props = { ...baseProps };
    props.client.listWorkforce.mockResolvedValue({
      available: true,
      agents: props.agents,
      capabilities: { permittedActions: ["run"] },
    });
    props.client.listWorkforceAgentDeployments.mockResolvedValue({
      available: true,
      agent: { id: "agent-a" },
      deployment: { active: true, version: 4 },
    });
    render(<SchedulePanel {...props} />);
    await userEvent.click(
      screen.getByRole("button", { name: "Create schedule" }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Instruction" }),
      "Prepare the research brief",
    );
    await userEvent.click(screen.getByRole("combobox", { name: "Agent" }));
    await userEvent.click(
      screen.getByRole("option", { name: /Research agent/ }),
    );
    await userEvent.click(
      screen.getAllByRole("button", { name: "Create schedule" }).at(-1),
    );
    await waitFor(() =>
      expect(props.onCreate).toHaveBeenCalledWith(
        expect.objectContaining({
          automation: expect.objectContaining({
            schemaVersion: "omega-automation/v1",
            trigger: { kind: "time", label: "Scheduled time" },
            actions: [
              expect.objectContaining({
                kind: "run_agent",
                agentId: "agent-a",
              }),
            ],
          }),
        }),
      ),
    );
  });

  it("keeps unsupported external or multi-action automations read-only", async () => {
    const props = {
      ...baseProps,
      result: {
        available: true,
        schedules: [
          {
            id: "sched-1",
            title: "Webhook",
            prompt: "Do work",
            cadence: "daily",
            enabled: true,
            automation: {
              schemaVersion: "omega-automation/v1",
              brief: "Webhook",
              trigger: { kind: "plugin", label: "Webhook" },
              actions: [
                { kind: "run_agent", label: "Run", agentId: "agent-a" },
                { kind: "post_room", label: "Post" },
              ],
              attachments: [],
            },
          },
        ],
      },
      selectedId: "sched-1",
      detail: {
        available: true,
        schedule: {
          id: "sched-1",
          title: "Webhook",
          prompt: "Do work",
          cadence: "daily",
          enabled: true,
          automation: {
            schemaVersion: "omega-automation/v1",
            brief: "Webhook",
            trigger: { kind: "plugin", label: "Webhook" },
            actions: [
              { kind: "run_agent", label: "Run", agentId: "agent-a" },
              { kind: "post_room", label: "Post" },
            ],
            attachments: [],
          },
        },
        runs: [],
      },
    };
    render(<SchedulePanel {...props} />);
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(
      screen.getByText(/external trigger or multiple actions/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Save schedule" }),
    ).toBeDisabled();
    expect(props.onUpdate).not.toHaveBeenCalled();
  });

  it("preserves supported automation fields and exact timezone cadence on edit", async () => {
    const automation = {
      schemaVersion: "omega-automation/v1",
      brief: "Retained brief",
      trigger: { kind: "time", label: "Morning" },
      actions: [
        {
          kind: "run_agent",
          label: "Research",
          agentId: "agent-a",
          agentName: "Research agent",
          instruction: "Keep this instruction",
        },
      ],
      attachments: [
        { kind: "workspace", id: "workspace-a", name: "Operations" },
      ],
    };
    const cadence = {
      kind: "cron",
      expr: "0 9 * * *",
      tz: "America/Los_Angeles",
    };
    const schedule = {
      id: "sched-1",
      title: "Brief",
      prompt: "Prepare brief",
      cadence,
      automation,
      enabled: true,
    };
    const props = {
      ...baseProps,
      result: { available: true, schedules: [schedule] },
    };
    props.client.listWorkforce.mockResolvedValue({
      available: true,
      agents: props.agents,
      capabilities: { permittedActions: ["run"] },
    });
    props.client.listWorkforceAgentDeployments.mockResolvedValue({
      available: true,
      agent: { id: "agent-a" },
      deployment: { active: true, version: 4 },
    });
    render(<SchedulePanel {...props} />);
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await userEvent.type(
      screen.getByRole("textbox", { name: "Title" }),
      " edited",
    );
    expect(
      screen.getByText("Uses the current active deployment"),
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Save schedule" }),
    );
    await waitFor(() =>
      expect(props.onUpdate).toHaveBeenCalledWith(
        "sched-1",
        expect.objectContaining({ automation, cadence, title: "Brief edited" }),
      ),
    );
  });

  it("preserves a default-agent schedule and does not invent an automation", async () => {
    const onCreate = vi.fn().mockResolvedValue({ available: true });
    render(<SchedulePanel {...baseProps} onCreate={onCreate} />);
    await userEvent.click(
      screen.getByRole("button", { name: "Create schedule" }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Instruction" }),
      "Default report",
    );
    await userEvent.click(
      screen.getAllByRole("button", { name: "Create schedule" }).at(-1),
    );
    await waitFor(() =>
      expect(onCreate).toHaveBeenCalledWith({
        prompt: "Default report",
        cadence: "daily",
        enabled: true,
      }),
    );
    expect(baseProps.client.listWorkforce).not.toHaveBeenCalled();
  });

  it("refuses stale deployment or lost permissions with an actionable message", async () => {
    const props = { ...baseProps, onCreate: vi.fn() };
    props.client.listWorkforce.mockResolvedValue({
      available: true,
      agents: props.agents,
      capabilities: { permittedActions: [] },
    });
    render(<SchedulePanel {...props} />);
    await userEvent.click(
      screen.getByRole("button", { name: "Create schedule" }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Instruction" }),
      "Brief",
    );
    await userEvent.click(screen.getByRole("combobox", { name: "Agent" }));
    await userEvent.click(
      screen.getByRole("option", { name: /Research agent/ }),
    );
    await userEvent.click(
      screen.getAllByRole("button", { name: "Create schedule" }).at(-1),
    );
    expect(
      await screen.findByText(/agent or your run permissions changed/),
    ).toBeInTheDocument();
    expect(props.onCreate).not.toHaveBeenCalled();
  });

  it("does not submit a stale target after the selection changes during preflight", async () => {
    let resolveWorkforce;
    const props = { ...baseProps, onCreate: vi.fn() };
    props.client.listWorkforce.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveWorkforce = resolve;
        }),
    );
    render(<SchedulePanel {...props} />);
    await userEvent.click(
      screen.getByRole("button", { name: "Create schedule" }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Instruction" }),
      "Brief",
    );
    await userEvent.click(screen.getByRole("combobox", { name: "Agent" }));
    await userEvent.click(
      screen.getByRole("option", { name: /Research agent/ }),
    );
    await userEvent.click(
      screen.getAllByRole("button", { name: "Create schedule" }).at(-1),
    );
    await userEvent.click(screen.getByRole("combobox", { name: "Agent" }));
    await userEvent.click(
      screen.getByRole("option", { name: "Workspace default agent" }),
    );
    resolveWorkforce({
      available: true,
      agents: props.agents,
      capabilities: { permittedActions: ["run"] },
    });
    await waitFor(() =>
      expect(props.client.listWorkforceAgentDeployments).not.toHaveBeenCalled(),
    );
    expect(props.onCreate).not.toHaveBeenCalled();
    expect(props.client.listWorkforceAgentDeployments).not.toHaveBeenCalled();
  });

  it("shows observed execution identity in run history", () => {
    const props = {
      ...baseProps,
      selectedId: "sched-1",
      detail: {
        available: true,
        schedule: {
          id: "sched-1",
          title: "Report",
          prompt: "Report",
          cadence: "daily",
          enabled: true,
          runCount: 1,
        },
        runs: [
          {
            id: "run-1",
            status: "fired",
            firedAt: "2026-10-12T00:00:00Z",
            execution: {
              agentId: "agent-a",
              agentVersion: 3,
              deploymentVersion: 8,
              deploymentProcessId: "proc-8",
            },
            receipt: { seq: 1 },
          },
        ],
      },
    };
    render(<SchedulePanel {...props} />);
    expect(
      screen.getByText(
        /agent agent-a · agent v3 · deployment v8 · process proc-8/,
      ),
    ).toBeInTheDocument();
  });
});
