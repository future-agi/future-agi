import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import { NOT_IN_RELEASE, NOT_IN_RELEASE_SHORT } from "../notInRelease";
import NotInReleaseChip from "../NotInReleaseChip";
import NotInReleaseButton from "../NotInReleaseButton";

const LINE = "Not available in this release";

describe("the shared Not available in this release line", () => {
  it("is one exported constant with the exact wording", () => {
    expect(NOT_IN_RELEASE).toBe(LINE);
    expect(NOT_IN_RELEASE).not.toMatch(/coming[\s-]soon/i);
  });

  it("renders as visible chip text a screen reader reads", () => {
    render(<NotInReleaseChip />);
    expect(screen.getByText(LINE)).toBeVisible();
  });

  // Three whole-line chips cannot fit a
  // tab strip at 1024 px, so a tab's chip shows a short word. It makes no
  // promise about when, and the whole line stays what a screen reader
  // reads and what the tooltip shows.
  it("has a short chip that shows a short word, makes no promise, and keeps the whole line for a screen reader and in its tooltip", async () => {
    expect(NOT_IN_RELEASE_SHORT).toBe("N/A");
    expect(NOT_IN_RELEASE_SHORT).not.toMatch(/soon|later|coming|next|yet/i);
    render(
      <button type="button" disabled>
        Constraints <NotInReleaseChip short />
      </button>,
    );
    expect(screen.getByText(NOT_IN_RELEASE_SHORT)).toBeVisible();
    expect(screen.getByText(NOT_IN_RELEASE_SHORT)).toHaveAttribute(
      "aria-hidden",
      "true",
    );
    expect(screen.getByRole("button")).toHaveAccessibleName(
      `Constraints ${LINE}`,
    );
    const chip = screen.getByText(LINE).closest("[data-not-in-release]");
    fireEvent.mouseOver(chip);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(LINE);
  });

  it("keeps a small action disabled, out of the tab order and described by the line", () => {
    const onClick = vi.fn();
    render(
      <NotInReleaseButton onClick={onClick}>Choose winner</NotInReleaseButton>,
    );
    const button = screen.getByRole("button", { name: "Choose winner" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-disabled", "true");
    expect(button).toHaveAccessibleDescription(LINE);
    fireEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("can carry a more specific reason, still disabled and never clickable", () => {
    const onClick = vi.fn();
    render(
      <NotInReleaseButton
        reason="Fork this environment to edit."
        onClick={onClick}
      >
        Add scenarios
      </NotInReleaseButton>,
    );
    const button = screen.getByRole("button", { name: "Add scenarios" });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription(
      "Fork this environment to edit.",
    );
    fireEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });
});
