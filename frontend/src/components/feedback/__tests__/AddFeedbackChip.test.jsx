import { describe, it, expect, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import AddFeedbackChip from "../AddFeedbackChip";

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-testid="iconify" data-icon={icon} />,
}));

describe("AddFeedbackChip", () => {
  it("renders the icon + label and fires onClick when enabled", async () => {
    const onClick = vi.fn();
    render(<AddFeedbackChip onClick={onClick} />);

    expect(screen.getByText("Add feedback")).toBeInTheDocument();
    expect(screen.getByTestId("iconify")).toHaveAttribute(
      "data-icon",
      "mdi:message-plus-outline",
    );

    await userEvent.setup().click(screen.getByTestId("add-feedback-chip"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("renders with pointer-events: none + the tooltip-when-disabled when disabled", () => {
    const onClick = vi.fn();
    render(
      <AddFeedbackChip
        onClick={onClick}
        disabled
        tooltipWhenDisabled="Eval errored — feedback unavailable"
      />,
    );

    const chip = screen.getByTestId("add-feedback-chip");
    // MUI's sx → computed CSS is normalized to lowercase. The disabledSx
    // block sets pointer-events: none + opacity: 0.4 — both are the only
    // gate keeping click events off the handler in production. Asserting
    // them directly is the right contract; testing-library's userEvent
    // refuses to even attempt a click on pointer-events: none elements
    // (matches browser semantics), so we don't need to simulate the click.
    const style = window.getComputedStyle(chip);
    expect(style.pointerEvents).toBe("none");
    expect(parseFloat(style.opacity)).toBeLessThan(1);

    // Even though we never click, the tooltip text must still be reachable
    // by AT/screen readers via the wrapping <span>. Confirm it landed in
    // the DOM tree.
    expect(
      chip.closest("span")?.getAttribute("aria-label") ??
        document.querySelector('[aria-label*="Eval errored"]'),
    ).toBeTruthy();
  });

  it("stopPropagation prevents the click from bubbling to a parent listener", async () => {
    const onClick = vi.fn();
    const onParentClick = vi.fn();
    render(
      <div onClick={onParentClick} data-testid="parent">
        <AddFeedbackChip onClick={onClick} />
      </div>,
    );

    await userEvent.setup().click(screen.getByTestId("add-feedback-chip"));
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(onParentClick).not.toHaveBeenCalled();
  });
});
