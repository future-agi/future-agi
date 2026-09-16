import { fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { render, screen } from "src/utils/test-utils";
import AskUserQuestionCard from "../read-audit/AskUserQuestionCard";

// A choice question with three explicit options. The card renders one row per
// option plus a trailing "Other" row.
const CHOICE = {
  id: "tool-side-effects",
  kind: "choice",
  title: "How should side-effecting tools behave?",
  why: "verify_identity mutates state",
  options: [
    { id: "read-only", label: "Read-only" },
    { id: "sandboxed", label: "Sandboxed" },
    { id: "live", label: "Live" },
  ],
};

const baseProps = (overrides = {}) => ({
  step: 0,
  total: 2,
  question: CHOICE,
  answer: null,
  onPick: vi.fn(),
  onOtherChange: vi.fn(),
  onBack: vi.fn(),
  onSkip: vi.fn(),
  onNext: vi.fn(),
  onBuild: vi.fn(),
  canSubmit: false,
  isLast: false,
  skipped: false,
  openCount: 2,
  resolvedCount: 0,
  skippedCount: 0,
  ...overrides,
});

describe("AskUserQuestionCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders one row per option plus Other, numbered 1-4, with the step badge", () => {
    render(<AskUserQuestionCard {...baseProps()} />);

    expect(screen.getByText("Read-only")).toBeInTheDocument();
    expect(screen.getByText("Sandboxed")).toBeInTheDocument();
    expect(screen.getByText("Live")).toBeInTheDocument();
    expect(screen.getByText("Other")).toBeInTheDocument();

    ["1", "2", "3", "4"].forEach((n) => {
      expect(screen.getByText(n)).toBeInTheDocument();
    });
    expect(screen.getByText("1/2")).toBeInTheDocument();
  });

  it("picks an option by index when its row is clicked", async () => {
    const props = baseProps();
    render(<AskUserQuestionCard {...props} />);

    await userEvent.click(screen.getByRole("button", { name: /Sandboxed/ }));
    expect(props.onPick).toHaveBeenCalledWith(1);
  });

  it("picks an option when Enter is pressed on a focused row", () => {
    const props = baseProps();
    render(<AskUserQuestionCard {...props} />);

    const row = screen.getByRole("button", { name: /Read-only/ });
    fireEvent.keyDown(row, { key: "Enter" });
    expect(props.onPick).toHaveBeenCalledWith(0);
  });

  it("relays every keystroke of the Other field, including spaces", async () => {
    const props = baseProps();
    render(<AskUserQuestionCard {...props} />);

    const field = screen.getByPlaceholderText("Type your own answer here");
    await userEvent.type(field, "a b");
    // The controlled value never updates, so each keystroke fires on its own —
    // asserting the space landed proves the row's onKeyDown didn't swallow it.
    expect(props.onOtherChange).toHaveBeenCalledWith(" ");
  });

  it("marks the Other row pressed when a free-text answer is present", () => {
    render(<AskUserQuestionCard {...baseProps({ answer: { other: "x" } })} />);
    expect(screen.getByRole("button", { name: /Other/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("gates Next on canSubmit and drops Back when onBack is null", () => {
    const props = baseProps({ canSubmit: false, onBack: null });
    const { rerender } = render(<AskUserQuestionCard {...props} />);

    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Back" })).not.toBeInTheDocument();

    rerender(<AskUserQuestionCard {...baseProps({ canSubmit: true, onBack: null })} />);
    expect(screen.getByRole("button", { name: "Next" })).toBeEnabled();
  });

  it("skips the question when Skip is clicked", async () => {
    const props = baseProps();
    render(<AskUserQuestionCard {...props} />);

    await userEvent.click(screen.getByRole("button", { name: "Skip" }));
    expect(props.onSkip).toHaveBeenCalledTimes(1);
  });

  it("disables the build on the last question while it is blocked, with the blocking tooltip", async () => {
    render(
      <AskUserQuestionCard
        {...baseProps({ isLast: true, openCount: 1, canSubmit: false, skipped: false })}
      />,
    );

    const build = screen.getByRole("button", { name: /Build the environment/ });
    expect(build).toBeDisabled();

    fireEvent.mouseOver(build.parentElement);
    expect(await screen.findByText("1 open question still block the build")).toBeInTheDocument();
  });

  it("builds from the last question when nothing is open", async () => {
    const props = baseProps({ isLast: true, openCount: 0, canSubmit: true });
    render(<AskUserQuestionCard {...props} />);

    const build = screen.getByRole("button", { name: /Build the environment/ });
    expect(build).toBeEnabled();
    await userEvent.click(build);
    expect(props.onBuild).toHaveBeenCalledTimes(1);
  });

  it("shows the skipped INFERRED-marker strip", () => {
    render(<AskUserQuestionCard {...baseProps({ skipped: true })} />);
    expect(
      screen.getByText("Skipped — the affected fact will carry an INFERRED marker."),
    ).toBeInTheDocument();
  });
});
