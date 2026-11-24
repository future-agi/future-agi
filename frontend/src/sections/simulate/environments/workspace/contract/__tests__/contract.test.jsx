import { describe, it, expect, vi, beforeEach } from "vitest";
import userEvent from "@testing-library/user-event";
import { enqueueSnackbar } from "notistack";
import { render, screen, within } from "src/utils/test-utils";
import useSimulationAccess from "../../../hooks/useSimulationAccess";
import RlContractPanel from "../RlContractPanel";

vi.mock("../../../hooks/useSimulationAccess", () => ({ default: vi.fn() }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const env = {
  name: "Refund Copilot",
  rules: [
    "Never disclose another caller's data.",
    "Only refund verified callers.",
  ],
  useCases: ["Refund a late order", "Cancel a booking"],
  endConditions: {
    ended_reasons: ["customer-ended-call", "silence-timed-out"],
    max_turns: 10,
    max_duration_seconds: 3600,
    clock: "real-time",
  },
};

const liveChat = (extra = {}) => ({
  send: vi.fn(),
  busy: false,
  live: true,
  ...extra,
});

const renderPanel = (props = {}) =>
  render(<RlContractPanel env={env} backed {...props} />);

beforeEach(() => {
  vi.mocked(useSimulationAccess).mockReturnValue({
    canEdit: true,
    canRun: true,
  });
  vi.mocked(enqueueSnackbar).mockClear();
});

describe("RlContractPanel", () => {
  it("lists the rules from the agent's prompt, numbered by position", () => {
    renderPanel();
    expect(screen.getByText("Rules (2)")).toBeInTheDocument();
    expect(screen.getByText("From the agent's prompt")).toBeInTheDocument();
    expect(screen.getByText("R1")).toBeInTheDocument();
    expect(screen.getByText("R2")).toBeInTheDocument();
    expect(
      screen.getByText("Only refund verified callers."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/sub-goal/)).toBeNull();
    expect(screen.getAllByText("Not tested")).toHaveLength(2);
  });

  it("shows how many scenarios cover a rule through its sub-goal and links to them", async () => {
    const user = userEvent.setup();
    const onGo = vi.fn();
    renderPanel({
      env: {
        ...env,
        ruleDetails: [
          {
            text: "Only refund verified callers.",
            sub_goal: "identity_checked",
          },
        ],
      },
      scenarios: [
        { subTasks: [{ id: "identity_checked" }] },
        { subTasks: [{ id: "refund_issued" }] },
        { subTasks: [{ id: "refund_issued" }, { id: "identity_checked" }] },
      ],
      onGo,
    });

    expect(screen.getAllByText("Not tested")).toHaveLength(1);
    await user.click(screen.getByText("1 sub-goal · 2 scenarios"));
    expect(onGo).toHaveBeenCalledWith("scenarios");
  });

  it("never counts zero scenarios on a rule's coverage", () => {
    renderPanel({
      env: {
        ...env,
        ruleDetails: [
          {
            text: "Only refund verified callers.",
            sub_goal: "identity_checked",
          },
        ],
      },
      scenarios: [],
    });

    expect(screen.getByText("1 sub-goal")).toBeInTheDocument();
    expect(screen.queryByText(/0 scenarios/)).toBeNull();
  });

  it("leaves the coverage out until the scenarios are written", () => {
    renderPanel({
      env: {
        ...env,
        ruleDetails: [
          {
            text: "Only refund verified callers.",
            sub_goal: "identity_checked",
          },
        ],
      },
      coverageReady: false,
    });

    expect(screen.getByText("Rules (2)")).toBeInTheDocument();
    expect(screen.queryByText("Not tested")).toBeNull();
    expect(screen.queryByText(/sub-goal/)).toBeNull();
  });

  it("leaves the rules card out when there are no rules and nothing to add them with", () => {
    renderPanel({ env: { ...env, rules: [] } });
    expect(screen.queryByText(/^Rules/)).toBeNull();
  });

  it("keeps an empty rules card when a rule can be added, with no zero count", () => {
    renderPanel({ env: { ...env, rules: [] }, chat: liveChat() });
    expect(screen.getByText("Rules")).toBeInTheDocument();
    expect(screen.queryByText("Rules (0)")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Add rule" }),
    ).toBeInTheDocument();
  });

  it("lists the use cases", () => {
    renderPanel();
    expect(screen.getByText("Use cases (2)")).toBeInTheDocument();
    expect(screen.getByText("U1")).toBeInTheDocument();
    expect(screen.getByText("Cancel a booking")).toBeInTheDocument();
  });

  it("no longer shows the world, tools or capability graph", () => {
    renderPanel({
      env: {
        ...env,
        worldTables: [{ name: "orders", rows: 1, fields: [] }],
        toolStatus: [{ tool: "lookup_order", status: "simulated" }],
      },
    });
    expect(screen.queryByText(/^World/)).toBeNull();
    expect(screen.queryByText(/^Tools/)).toBeNull();
    expect(screen.queryByText("Capability graph")).toBeNull();
  });

  it("says when a conversation ends, from the real end conditions", () => {
    renderPanel();
    expect(screen.getByText("When a conversation ends")).toBeInTheDocument();
    expect(screen.getByText("It ends")).toBeInTheDocument();
    expect(screen.getByText("customer-ended-call")).toBeInTheDocument();
    expect(screen.getByText("It's cut off")).toBeInTheDocument();
    expect(screen.getByText("10 agent turns")).toBeInTheDocument();
    expect(screen.getByText("60 minutes")).toBeInTheDocument();
    expect(screen.getByText("Time · Real time")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Pauses and slow replies are part of the test, so time runs as it would on a real call.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Same starting data every time")).toBeNull();
  });

  it("says the end conditions are not authored yet instead of showing sample ones", () => {
    renderPanel({ env: { ...env, endConditions: null } });
    expect(screen.getByText("Not authored yet")).toBeInTheDocument();
    expect(
      screen.getByText(
        /The build writes these once it has read how a run of this agent ends/,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("It ends")).toBeNull();
  });

  it("says a template or fork has no end conditions because it was never built", () => {
    renderPanel({ env: { ...env, endConditions: null }, backed: false });
    expect(
      screen.getByText("This environment hasn't been built, so it has none."),
    ).toBeInTheDocument();
  });

  it("offers no actions without a builder chat to send them through", () => {
    renderPanel();
    expect(screen.queryByRole("button", { name: /Add rule/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Edit rule R1" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Remove rule R1" })).toBeNull();
  });

  it("offers no actions on a locked template", () => {
    renderPanel({ chat: liveChat(), locked: true });
    expect(screen.queryByRole("button", { name: /Add rule/ })).toBeNull();
  });

  it("edits a rule through the builder chat with one precise instruction", async () => {
    const user = userEvent.setup();
    const chat = liveChat();
    renderPanel({ chat });

    await user.click(screen.getByRole("button", { name: "Edit rule R2" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Edit rule R2")).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        /lands as a draft that you save as a new environment version/,
      ),
    ).toBeInTheDocument();

    const field = within(dialog).getByRole("textbox", { name: "Rule" });
    await user.clear(field);
    await user.type(field, "Only refund callers who passed verification.");
    await user.click(
      within(dialog).getByRole("button", { name: "Ask the builder" }),
    );

    expect(chat.send).toHaveBeenCalledWith(
      'Change rule 2 of the contract from "Only refund verified callers." to "Only refund callers who passed verification.". Update the sub-goals and checks that depend on it, and keep everything else as it is.',
    );
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "The builder is updating the contract. The change shows as a draft for you to save.",
      { variant: "info" },
    );
  });

  it("adds a rule through the builder chat", async () => {
    const user = userEvent.setup();
    const chat = liveChat();
    renderPanel({ chat });

    await user.click(screen.getByRole("button", { name: /Add rule/ }));
    const dialog = screen.getByRole("dialog");
    await user.type(
      within(dialog).getByRole("textbox", { name: "Rule" }),
      "Confirm the amount before refunding.",
    );
    await user.click(
      within(dialog).getByRole("button", { name: "Ask the builder" }),
    );
    expect(chat.send).toHaveBeenCalledWith(
      'Add this rule to the contract: "Confirm the amount before refunding.". Add the sub-goals and checks it needs, and keep everything else as it is.',
    );
  });

  it("removes a use case after a confirm", async () => {
    const user = userEvent.setup();
    const chat = liveChat();
    renderPanel({ chat });

    await user.click(
      screen.getByRole("button", { name: "Remove use case U1" }),
    );
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Remove use case U1")).toBeInTheDocument();
    await user.click(
      within(dialog).getByRole("button", { name: "Remove use case" }),
    );
    expect(chat.send).toHaveBeenCalledWith(
      'Remove use case 1 ("Refund a late order") from the contract. Remove or update the scenarios and checks that depend on it, and keep everything else as it is.',
    );
  });

  it("sends nothing when the dialog is cancelled", async () => {
    const user = userEvent.setup();
    const chat = liveChat();
    renderPanel({ chat });
    await user.click(screen.getByRole("button", { name: /Add use case/ }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(chat.send).not.toHaveBeenCalled();
  });

  it("disables the actions for a viewer", () => {
    vi.mocked(useSimulationAccess).mockReturnValue({
      canEdit: false,
      canRun: false,
    });
    renderPanel({ chat: liveChat() });
    expect(screen.getByRole("button", { name: /Add rule/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Edit rule R1" })).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Remove rule R1" }),
    ).toBeDisabled();
  });

  it("disables the actions while the builder is busy", () => {
    renderPanel({ chat: liveChat({ busy: true }) });
    expect(screen.getByRole("button", { name: /Add rule/ })).toBeDisabled();
  });

  it("disables the actions until the environment is ready", () => {
    renderPanel({ chat: liveChat({ live: false }) });
    expect(screen.getByRole("button", { name: /Add use case/ })).toBeDisabled();
  });
});
