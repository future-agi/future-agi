import { describe, it, expect, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import Share from "../Share";

describe("Share", () => {
  it("does not present the confirmation button as a primary save action", () => {
    const onClose = vi.fn();
    render(
      <Share
        open
        body="Anyone with the link will be able to view the data."
        onClose={onClose}
      />,
    );

    const confirmButton = screen.getByLabelText("close-share-project");
    expect(confirmButton).toHaveTextContent("Close");
    expect(confirmButton).not.toHaveClass("MuiButton-contained");
    expect(confirmButton).toHaveClass("MuiButton-outlined");
    expect(
      screen.queryByLabelText("finish-share-project"),
    ).not.toBeInTheDocument();
  });

  it("tells the user the link has no access control", () => {
    render(<Share open body="Share this link." onClose={vi.fn()} />);

    expect(
      screen.getByText(
        "Note: This creates a direct link without access control.",
      ),
    ).toBeInTheDocument();
  });

  it("still just closes the dialog when the confirmation button is clicked", async () => {
    const onClose = vi.fn();
    render(<Share open body="Share this link." onClose={onClose} />);

    await userEvent.click(screen.getByLabelText("close-share-project"));

    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
