import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import { nudgeFromApi } from "src/api/simulate-environments/versions";
import NudgeCard from "../NudgeCard";

const raw = (over = {}) => ({
  id: "n1",
  target: "environment",
  state: "queued",
  facts: { prompt_changed: true, agent_default_moved: true },
  trigger_agent_version: { id: "a3", label: "v3" },
  base_environment_version: { id: "e4", label: "v4" },
  replaced_by: null,
  can_stop: true,
  queued_behind_input: false,
  summary: null,
  error: null,
  ...over,
});
const turnFor = (over = {}, extra = {}) => ({
  id: "m1",
  role: "system",
  kind: "nudge",
  nudgeId: "n1",
  text: "Agent v3 changed: prompt. Rebuild the environment?",
  nudge: nudgeFromApi(raw(over)),
  nudgesEnabled: true,
  onRebuild: vi.fn(),
  onSkipNudge: vi.fn(),
  onStopNudge: vi.fn(),
  ...extra,
});
const status = () => screen.getByRole("status");
const stopButton = () =>
  screen.getByRole("button", { name: "Stop the update: Agent v3 changed" });

describe("NudgeCard while a rebuild waits or runs", () => {
  it("waits for the chat, with Stop", async () => {
    const user = userEvent.setup();
    const turn = turnFor();
    render(<NudgeCard turn={turn} />);
    expect(status()).toHaveTextContent("Waiting for the chat to finish");
    expect(screen.queryByRole("button", { name: /Skip/ })).toBeNull();
    await user.click(stopButton());
    expect(turn.onStopNudge).toHaveBeenCalledTimes(1);
  });

  it("waits for the user's answer in chat", () => {
    render(<NudgeCard turn={turnFor({ queued_behind_input: true })} />);
    expect(status()).toHaveTextContent("Waiting for your answer in chat");
  });

  it("shows the update running, with Stop even when rebuilding is off", () => {
    render(
      <NudgeCard
        turn={turnFor({ state: "running" }, { nudgesEnabled: false })}
      />,
    );
    expect(status()).toHaveTextContent("Updating…");
    expect(stopButton()).toBeEnabled();
  });

  it("shows Stop pending, and a refused Stop", () => {
    const { rerender } = render(
      <NudgeCard turn={turnFor({ state: "running" }, { stopping: true })} />,
    );
    expect(screen.getByRole("button", { name: "Stopping…" })).toBeDisabled();
    rerender(
      <NudgeCard
        turn={turnFor({ state: "running" }, { error: { statusCode: 500 } })}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Couldn't stop the update. Try again.",
    );
  });

  it("names the unsaved edits it set aside", () => {
    render(
      <NudgeCard
        turn={turnFor({
          summary: {
            set_aside: { draft_version_id: "d1", started_from_label: "v2" },
          },
        })}
      />,
    );
    expect(
      screen.getByText("Your unsaved edits from v2 were set aside."),
    ).toBeInTheDocument();
  });
});

describe("NudgeCard before a rebuild", () => {
  it("says the draft's unsaved edits go live with it", () => {
    const draft = { stale: false, changes: { tools: 2, rules: 1 } };
    render(
      <NudgeCard
        turn={turnFor(
          { state: "pending" },
          { draft: { ...draft, lastEditedBy: "Asha" } },
        )}
      />,
    );
    expect(
      screen.getByText(
        "This also makes the unsaved edits in the draft live: 3 changes, last edited by Asha.",
      ),
    ).toBeInTheDocument();
  });

  it("says a stale draft is set aside", () => {
    render(
      <NudgeCard
        turn={turnFor(
          { state: "pending" },
          { draft: { stale: true, parentLabel: "v2", changes: {} } },
        )}
      />,
    );
    expect(
      screen.getByText(
        "Your unsaved edits started from v2; Rebuild sets them aside.",
      ),
    ).toBeInTheDocument();
  });
});

describe("NudgeCard once a rebuild lands", () => {
  const applied = (summary = {}, over = {}) =>
    turnFor({
      state: "applied",
      summary: {
        piece: "environment",
        version_id: "e5",
        label: "v5",
        line: "1 tool added",
        update: {
          contract: { tools: { added: ["refund_order"] } },
          sub_goals: { changed: ["verify_identity"] },
        },
        draft_edits: {
          contract: { use_cases: { added: ["late_delivery"] } },
          edited_by: "Asha",
        },
        both: [{ kind: "environment", item: "refund_order" }],
        agent_default_moved: true,
        ...summary,
      },
      ...over,
    });

  it("shows the version, its line and the three buckets by name", async () => {
    const user = userEvent.setup();
    render(<NudgeCard turn={applied()} />);
    expect(status()).toHaveTextContent("Rebuilt: environment v5");
    expect(screen.getByText("1 tool added")).toBeInTheDocument();
    expect(
      screen.getByText("Agent v3 is now the default."),
    ).toBeInTheDocument();

    const edits = screen.getByText("Your unsaved edits (by Asha)");
    await user.click(edits);
    const editsBox = within(edits.closest("details"));
    expect(editsBox.getByText("late_delivery")).toBeVisible();

    const update = screen.getByText("This update");
    await user.click(update);
    const updateBox = within(update.closest("details"));
    expect(updateBox.getByText("refund_order")).toBeVisible();
    expect(updateBox.getByText("verify_identity")).toBeVisible();

    const both = screen.getByText("Changed by both");
    await user.click(both);
    expect(
      within(both.closest("details")).getByText("refund_order"),
    ).toBeVisible();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("keeps a rebuilt suite's scenario keys as written", async () => {
    const user = userEvent.setup();
    render(
      <NudgeCard
        turn={turnFor({
          target: "suite",
          state: "applied",
          summary: {
            piece: "suite",
            label: "v7",
            update: {
              added: ["late_refund_case"],
              changed: { angry_caller_v2: ["persona"] },
            },
            draft_edits: null,
            both: [],
          },
        })}
      />,
    );
    expect(status()).toHaveTextContent("Rebuilt: scenarios v7");
    await user.click(screen.getByText("This update"));
    expect(screen.getByText("late_refund_case")).toBeVisible();
    expect(screen.getByText("angry_caller_v2")).toBeVisible();
    expect(screen.queryByText("Your unsaved edits")).toBeNull();
  });

  it("lists what was reused, left out, not checked and warned", () => {
    render(
      <NudgeCard
        turn={applied({
          reused: { version_id: "e2", label: "v2" },
          left_out: [{ key: "refund_policy", why: "needs a world file" }],
          not_checked: ["tone"],
          warnings: ["The source review is out of date."],
        })}
      />,
    );
    expect(
      screen.getByText("Same as v2, which is the default again."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Left out: refund_policy — needs a world file"),
    ).toBeInTheDocument();
    expect(screen.getByText("Not checked: tone")).toBeInTheDocument();
    expect(
      screen.getByText("The source review is out of date."),
    ).toBeInTheDocument();
  });

  it.each([
    [
      "not_prepared",
      "Agent v3 isn't prepared to run here yet. Make it the default from the Agents panel.",
    ],
    [
      "default_moved_by_hand",
      "The default agent was changed meanwhile, so agent v3 didn't become the default.",
    ],
    ["not_pickable", "Agent v3 can't be made the default right now."],
    [
      "rollback_unavailable",
      "Agent v3 didn't become the default. Make it the default from the Agents panel.",
    ],
  ])("says why the agent didn't become the default (%s)", (reason, text) => {
    render(
      <NudgeCard
        turn={turnFor({
          state: "no_change",
          summary: {
            line: "v4 already fits v3",
            agent_default_moved: false,
            agent_not_moved: reason,
          },
        })}
      />,
    );
    expect(status()).toHaveTextContent("v4 already fits v3");
    expect(screen.getByText(text)).toBeInTheDocument();
    expect(screen.queryByText("Agent v3 is now the default.")).toBeNull();
  });
});

describe("NudgeCard once a rebuild fails", () => {
  const failed = (error, extra) => turnFor({ state: "failed", error }, extra);

  it.each([
    ["stopped_by_user", "Stopped"],
    ["no_report", "The builder ended without reporting what it changed."],
    ["checks_failed", "The update didn't pass its checks."],
    ["not_runnable", "The updated environment couldn't be prepared to run."],
    ["turn_failed", "The builder's turn failed."],
    ["turn_interrupted", "The builder's turn was interrupted."],
    ["rebuild_not_started", "The update didn't start in time."],
    ["rebuild_stuck", "The update stopped making progress."],
    ["landing_failed", "The update couldn't be saved."],
    ["draft_changed", "Your unsaved edits changed while the update ran."],
    [
      "conversation_checkpoint_stale",
      "The environment changed while the update ran, so it wasn't saved.",
    ],
    [
      "scenario_key_taken",
      "The update added a scenario whose name is already taken.",
    ],
  ])("explains %s and offers Rebuild again", async (code, text) => {
    const user = userEvent.setup();
    const turn = failed({ code });
    render(<NudgeCard turn={turn} />);
    expect(status()).toHaveTextContent(text);
    expect(
      screen.getByText("Nothing changed; your unsaved edits are as they were."),
    ).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Rebuild again: Agent v3 changed" }),
    );
    expect(turn.onRebuild).toHaveBeenCalledTimes(1);
  });

  it("says set-aside edits stay set aside after a stop", () => {
    render(
      <NudgeCard
        turn={turnFor({
          state: "failed",
          error: { code: "stopped_by_user" },
          summary: {
            set_aside: { draft_version_id: "d1", started_from_label: "v2" },
          },
        })}
      />,
    );
    expect(
      screen.getByText("Your unsaved edits from v2 were set aside."),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Nothing changed. The edits that were set aside stay set aside.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/are as they were/)).toBeNull();
  });

  it("quotes the message of a code it doesn't know", () => {
    render(
      <NudgeCard
        turn={failed({ code: "some_new_code", message: "It broke." })}
      />,
    );
    expect(status()).toHaveTextContent("It broke.");
  });

  it("offers no Rebuild again once the environment is deleted", () => {
    render(<NudgeCard turn={failed({ code: "environment_deleted" })} />);
    expect(status()).toHaveTextContent("The environment was deleted.");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("says why Rebuild again can't be pressed while rebuilding is off", () => {
    render(
      <NudgeCard
        turn={failed({ code: "stopped_by_user" }, { nudgesEnabled: false })}
      />,
    );
    expect(
      screen.getByRole("button", { name: "Rebuild again: Agent v3 changed" }),
    ).toHaveAccessibleDescription(
      "Rebuilding is turned off for this environment.",
    );
  });

  it("tags the problems found in the user's own unsaved edits", () => {
    render(
      <NudgeCard
        turn={failed({
          code: "checks_failed",
          problems: [
            {
              item: "refund_order",
              kind: "tool",
              message: "has no handler",
              in_your_unsaved_edits: true,
            },
            {
              item: "verify_identity",
              kind: "sub_goal",
              message: "is unreachable",
              in_your_unsaved_edits: false,
            },
          ],
        })}
      />,
    );
    const items = screen.getAllByRole("listitem");
    expect(items[0]).toHaveTextContent(
      "refund_order: has no handler(in your unsaved edits)",
    );
    expect(items[1]).toHaveTextContent("verify_identity: is unreachable");
    expect(items[1]).not.toHaveTextContent("in your unsaved edits");
  });
});
