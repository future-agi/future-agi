import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import AskUserQuestionCard from "../AskUserQuestionCard";

// W10-SIMULATE-OTHER-U03: the builder's blocking question cards must be
// answerable from the keyboard and announce each option's checked state.
const single = {
  step: 1,
  total: 2,
  prompt: "How should refunds above $200 behave?",
  multiSelect: false,
  options: [
    { label: "Require supervisor approval", description: "A supervisor signs off first." },
    { label: "Auto-approve up to $500" },
    { label: "Always decline" },
  ],
};

const multi = {
  step: 2,
  total: 2,
  prompt: "Which channels should the agent cover?",
  multiSelect: true,
  options: [{ label: "Chat" }, { label: "Voice" }, { label: "Email" }],
};

describe("AskUserQuestionCard keyboard access", () => {
  it("lets a keyboard user pick a single-select option", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<AskUserQuestionCard question={single} onSubmit={onSubmit} />);

    const group = screen.getByRole("radiogroup", { name: single.prompt });
    expect(group).toBeInTheDocument();
    const radios = screen.getAllByRole("radio");
    expect(radios).toHaveLength(3);
    radios.forEach((r) => expect(r).toHaveAttribute("aria-checked", "false"));

    const first = screen.getByRole("radio", { name: "Require supervisor approval" });
    const second = screen.getByRole("radio", { name: "Auto-approve up to $500" });
    expect(first).toHaveAccessibleDescription("A supervisor signs off first.");

    // Tab lands on the option group, not straight on the "Other" text box.
    await user.tab();
    expect(first).toHaveFocus();

    await user.keyboard(" ");
    expect(first).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("button", { name: "Submit" })).toBeEnabled();

    // Arrow keys move the pick through the group.
    await user.keyboard("{ArrowDown}");
    expect(second).toHaveFocus();
    expect(second).toHaveAttribute("aria-checked", "true");
    expect(first).toHaveAttribute("aria-checked", "false");

    // One Tab stop for the whole group: the next Tab leaves it.
    await user.tab();
    expect(screen.getByPlaceholderText("Type your own answer here")).toHaveFocus();

    await user.click(screen.getByRole("button", { name: "Submit" }));
    expect(onSubmit).toHaveBeenCalledWith(["Auto-approve up to $500"]);
  });

  it("lets a keyboard user toggle multi-select options", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<AskUserQuestionCard question={multi} onSubmit={onSubmit} />);

    expect(screen.getByRole("group", { name: multi.prompt })).toBeInTheDocument();
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes).toHaveLength(3);
    expect(screen.getByRole("button", { name: "Submit" })).toBeDisabled();

    await user.tab();
    expect(screen.getByRole("checkbox", { name: "Chat" })).toHaveFocus();
    await user.keyboard(" ");

    await user.tab();
    await user.tab();
    const email = screen.getByRole("checkbox", { name: "Email" });
    expect(email).toHaveFocus();
    await user.keyboard("{Enter}");

    expect(screen.getByRole("checkbox", { name: "Chat" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("checkbox", { name: "Voice" })).toHaveAttribute("aria-checked", "false");
    expect(email).toHaveAttribute("aria-checked", "true");

    // Space toggles a picked option back off.
    await user.keyboard(" ");
    expect(email).toHaveAttribute("aria-checked", "false");
    await user.keyboard(" ");

    const submit = screen.getByRole("button", { name: "Submit" });
    expect(submit).toBeEnabled();
    submit.focus();
    await user.keyboard("{Enter}");
    expect(onSubmit).toHaveBeenCalledWith(["Chat", "Email"]);
  });
});
