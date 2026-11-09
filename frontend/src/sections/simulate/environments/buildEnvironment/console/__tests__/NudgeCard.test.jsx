import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import { nudgeFromApi } from "src/api/simulate-environments/versions";
import { Turn } from "../ConsoleTurn";
import BuilderConsole from "../BuilderConsole";
import { CONSOLE_COPY } from "../../build.constants";
import NudgeCard from "../NudgeCard";

const FACTS = {
  source: {
    kind: "github",
    changed: true,
    from: "abc1234567",
    to: "def7654321",
  },
  changed_files: [
    {
      path: "tools/orders.py",
      status: "modified",
      previous_path: null,
      additions: 3,
      deletions: 1,
    },
    {
      path: ".env",
      status: "modified",
      previous_path: null,
      additions: null,
      deletions: null,
    },
  ],
  files_total: 2,
  files_truncated: false,
  reversed: false,
  files_unavailable: null,
  prompt_changed: true,
  variables: {
    added: ["NEW_KEY"],
    removed: ["OLD_KEY"],
    changed: ["STRIPE_KEY"],
    unknown: ["REGION"],
  },
  settings_changed: ["mode"],
  agent_default_moved: true,
};
const raw = (over = {}) => ({
  id: "n1",
  target: "environment",
  state: "pending",
  facts: FACTS,
  trigger_agent_version: { id: "a3", label: "v3" },
  base_environment_version: { id: "e4", label: "v4" },
  replaced_by: null,
  ...over,
});
const turnFor = (over = {}, extra = {}) => ({
  id: "m1",
  role: "system",
  kind: "nudge",
  nudgeId: "n1",
  text: "Agent v3 changed: prompt. Rebuild the environment?",
  nudge: nudgeFromApi(raw(over)),
  onSkipNudge: vi.fn(),
  ...extra,
});
const card = () => screen.getByTestId("nudge-card-n1");

describe("NudgeCard", () => {
  it("names what changed: source, files, prompt, variables and settings", () => {
    render(<NudgeCard turn={turnFor()} />);
    const view = within(card());
    expect(view.getByText("Agent v3 changed")).toBeInTheDocument();
    expect(view.getByText("Commit abc1234 → def7654")).toBeInTheDocument();
    expect(
      view.getByText("tools/orders.py · modified +3 -1"),
    ).toBeInTheDocument();
    expect(view.getByText(".env · modified")).toBeInTheDocument();
    expect(view.getByText("Prompt changed")).toBeInTheDocument();
    expect(view.getByText("Added: NEW_KEY")).toBeInTheDocument();
    expect(view.getByText("Removed: OLD_KEY")).toBeInTheDocument();
    expect(view.getByText("Changed: STRIPE_KEY")).toBeInTheDocument();
    expect(view.getByText("May have changed: REGION")).toBeInTheDocument();
    expect(view.getByText("Settings changed: mode")).toBeInTheDocument();
    expect(view.getByText("Rebuild the environment?")).toBeInTheDocument();
  });

  it("lists the first eight files, then how many more", () => {
    const files = Array.from({ length: 10 }, (_, i) => ({
      path: `f${i}.py`,
      status: "added",
    }));
    render(
      <NudgeCard
        turn={turnFor({
          facts: { ...FACTS, changed_files: files, files_truncated: true },
        })}
      />,
    );
    expect(screen.getByText("f7.py · added")).toBeInTheDocument();
    expect(screen.queryByText("f8.py · added")).toBeNull();
    expect(screen.getByText("and 2 more")).toBeInTheDocument();
    expect(
      screen.getByText("Only some of the changed files are listed."),
    ).toBeInTheDocument();
  });

  it("says why the files aren't listed and names a roll back", () => {
    render(
      <NudgeCard
        turn={turnFor({
          facts: {
            ...FACTS,
            reversed: true,
            changed_files: [],
            files_unavailable: "not_linear",
          },
        })}
      />,
    );
    expect(
      screen.getByText("Rolled back to commit def7654"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "File changes aren't available (the history isn't linear)",
      ),
    ).toBeInTheDocument();
  });

  it("rebuilds with the keyboard, and shows the rebuild starting", async () => {
    const user = userEvent.setup();
    const turn = turnFor({}, { onRebuild: vi.fn(), nudgesEnabled: true });
    const { rerender } = render(<NudgeCard turn={turn} />);
    const rebuild = screen.getByRole("button", {
      name: "Rebuild: Agent v3 changed",
    });
    expect(rebuild).toBeEnabled();
    rebuild.focus();
    await user.keyboard("{Enter}");
    expect(turn.onRebuild).toHaveBeenCalledTimes(1);

    rerender(
      <NudgeCard turn={{ ...turn, answering: true, rebuilding: true }} />,
    );
    expect(screen.getByRole("button", { name: "Starting…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Skip" })).toBeDisabled();
  });

  it("says Rebuild is turned off when nudges are off, in plain text", () => {
    render(<NudgeCard turn={turnFor({}, { nudgesEnabled: false })} />);
    const rebuild = screen.getByRole("button", {
      name: "Rebuild: Agent v3 changed",
    });
    expect(rebuild).toBeDisabled();
    expect(rebuild).toHaveAccessibleDescription(
      "Rebuilding is turned off for this environment.",
    );
    expect(
      screen.getByText("Rebuilding is turned off for this environment."),
    ).toBeVisible();
  });

  it("skips with the keyboard, and shows the answer pending", async () => {
    const user = userEvent.setup();
    const turn = turnFor();
    const { rerender } = render(<NudgeCard turn={turn} />);
    const skip = screen.getByRole("button", { name: "Skip: Agent v3 changed" });
    skip.focus();
    await user.keyboard("{Enter}");
    expect(turn.onSkipNudge).toHaveBeenCalledTimes(1);

    rerender(<NudgeCard turn={{ ...turn, answering: true }} />);
    expect(screen.getByRole("button", { name: "Skipping…" })).toBeDisabled();
  });

  it.each([
    ["nudge_closed", "This update was already answered or closed."],
    ["nudges_disabled", "Rebuild isn't available yet."],
    ["rebuild_running", "An update is running; try again when it finishes"],
    ["environment_building", "The environment is still being built"],
    ["nudge_not_found", "This update no longer exists."],
  ])("explains a refused answer (%s)", (code, text) => {
    render(
      <NudgeCard
        turn={turnFor({}, { error: { statusCode: 409, error: code } })}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(text);
  });

  it("shows the usage message of a refused rebuild", () => {
    render(
      <NudgeCard
        turn={turnFor(
          {},
          {
            rebuilding: true,
            error: { statusCode: 402, message: "Your credits ran out." },
          },
        )}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Your credits ran out.",
    );
  });

  it("falls back to a retry message for any other failure", () => {
    render(<NudgeCard turn={turnFor({}, { error: { statusCode: 500 } })} />);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Couldn't skip. Try again.",
    );
  });

  it("moves focus to the state line once its own Skip lands", () => {
    const turn = turnFor();
    const { rerender } = render(<NudgeCard turn={turn} />);
    screen.getByRole("button", { name: "Skip: Agent v3 changed" }).focus();
    const answered = { nudge: nudgeFromApi(raw({ state: "answered_no" })) };

    rerender(<NudgeCard turn={{ ...turn, ...answered }} />);
    expect(screen.getByRole("status")).not.toHaveFocus();

    rerender(<NudgeCard turn={{ ...turn, ...answered, skipped: true }} />);
    expect(screen.getByRole("status")).toHaveFocus();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Skipped. The current version stays.",
    );
  });

  it("tells the chat once its state line has taken focus", () => {
    const onSkippedFocused = vi.fn();
    const answered = { nudge: nudgeFromApi(raw({ state: "answered_no" })) };
    render(
      <NudgeCard
        turn={{ ...turnFor(), ...answered, skipped: true, onSkippedFocused }}
      />,
    );
    expect(screen.getByRole("status")).toHaveFocus();
    expect(onSkippedFocused).toHaveBeenCalledTimes(1);
  });

  it("shows a skipped nudge's state and no buttons", () => {
    render(<NudgeCard turn={turnFor({ state: "answered_no" })} />);
    expect(screen.getByRole("status")).toHaveTextContent(
      "Skipped. The current version stays.",
    );
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByText("Rebuild the environment?")).toBeNull();
  });

  it.each([
    [
      { replaced_by: { id: "n2", trigger_label: "v5" } },
      "Replaced by agent v5",
    ],
    [
      { replaced_by: { id: "n2", trigger_label: "v3" } },
      "Updated for a newer environment version",
    ],
    [
      { target: "suite", replaced_by: { id: "n2", trigger_label: "v6" } },
      "Replaced by environment v6",
    ],
  ])("says what replaced it", (over, text) => {
    render(<NudgeCard turn={turnFor({ state: "replaced", ...over })} />);
    expect(screen.getByRole("status")).toHaveTextContent(text);
  });

  it.each([
    ["no_change", "No change needed"],
    ["failed", "Closed"],
  ])("shows the %s state", (state, text) => {
    render(<NudgeCard turn={turnFor({ state })} />);
    expect(screen.getByRole("status")).toHaveTextContent(text);
  });

  it("says a rebuild stopped by a newer one", () => {
    render(
      <NudgeCard
        turn={turnFor({
          state: "replaced",
          error: { code: "stopped_by_newer" },
        })}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "Stopped by a newer rebuild",
    );
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("offers a version that isn't this environment's default", () => {
    render(
      <NudgeCard
        turn={turnFor({ facts: { ...FACTS, agent_default_moved: false } })}
      />,
    );
    expect(screen.getByText("Agent v3 is available")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Rebuild updates this environment for it and makes it the default agent when the update lands.",
      ),
    ).toBeInTheDocument();
  });

  it("shows a suite nudge with the environment's summary", () => {
    render(
      <NudgeCard
        turn={turnFor({
          target: "suite",
          facts: {
            environment_diff: { "tools.get_order": {} },
            environment_summary: "1 tool added",
          },
        })}
      />,
    );
    expect(
      screen.getByText("The environment changed (v4)"),
    ).toBeInTheDocument();
    expect(screen.getByText("1 tool added")).toBeInTheDocument();
    expect(screen.getByText("Rebuild the scenarios?")).toBeInTheDocument();
  });

  it("falls back to the message text without the nudge", () => {
    render(<NudgeCard turn={turnFor({}, { nudge: undefined })} />);
    expect(
      screen.getByText("Agent v3 changed: prompt. Rebuild the environment?"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("Turn with a system turn", () => {
  it("renders a nudge card and a plain system line, neither with steps", () => {
    const { rerender } = render(<Turn turn={turnFor()} />);
    expect(card()).toBeInTheDocument();
    rerender(<Turn turn={{ id: "m2", role: "system", text: "Saved." }} />);
    expect(screen.getByText("Saved.")).toBeInTheDocument();
  });
});

describe("BuilderConsole with a nudge turn", () => {
  it("renders the card as the last turn and keeps the working indicator", () => {
    Element.prototype.scrollIntoView = vi.fn();
    render(
      <BuilderConsole
        turns={[{ id: "u1", role: "user", text: "hi" }, turnFor()]}
        running
        onSend={vi.fn()}
      />,
    );
    expect(card()).toBeInTheDocument();
    expect(screen.getByText(CONSOLE_COPY.workingDot)).toBeInTheDocument();
  });

  it("follows the transcript as it grows, not when a card changes", () => {
    const original = HTMLElement.prototype.scrollTo;
    const scrollTo = vi.fn();
    HTMLElement.prototype.scrollTo = scrollTo;
    try {
      const user = { id: "u1", role: "user", text: "hi" };
      const { rerender } = render(
        <BuilderConsole turns={[user, turnFor()]} onSend={vi.fn()} />,
      );
      scrollTo.mockClear();
      rerender(
        <BuilderConsole
          turns={[user, turnFor({}, { answering: true })]}
          onSend={vi.fn()}
        />,
      );
      expect(scrollTo).not.toHaveBeenCalled();
      rerender(
        <BuilderConsole
          turns={[user, turnFor(), { id: "u2", role: "user", text: "more" }]}
          onSend={vi.fn()}
        />,
      );
      expect(scrollTo).toHaveBeenCalled();
    } finally {
      HTMLElement.prototype.scrollTo = original;
    }
  });
});
