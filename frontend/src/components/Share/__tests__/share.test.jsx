import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import Share from "../Share";

const renderShare = (overrides = {}) => {
  const props = {
    open: true,
    title: "Share project",
    body: "Anyone with the link can view the selected runs.",
    onClose: vi.fn(),
    ...overrides,
  };

  render(<Share {...props} />);
  return props;
};

describe("Share", () => {
  it("presents the terminal action as a neutral Close button", () => {
    renderShare();

    const closeButton = screen.getByRole("button", {
      name: "close-share-project",
    });

    expect(closeButton).toHaveTextContent("Close");
    expect(closeButton).toHaveClass("MuiButton-outlined");
    expect(closeButton).toHaveClass("MuiButton-colorInherit");
    expect(
      screen.queryByRole("button", { name: "finish-share-project" }),
    ).not.toBeInTheDocument();
  });

  it("explains that the direct link has no access control", () => {
    renderShare();

    expect(
      screen.getByText(
        "Note: This creates a direct link without access control.",
      ),
    ).toBeInTheDocument();
  });

  it("keeps caller-provided body text and the Cancel action", () => {
    renderShare();

    expect(
      screen.getByText("Anyone with the link can view the selected runs."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Cancel-share-project" }),
    ).toBeInTheDocument();
  });

  it("closes the dialog from the terminal action", async () => {
    const props = renderShare();

    await userEvent.click(
      screen.getByRole("button", { name: "close-share-project" }),
    );

    expect(props.onClose).toHaveBeenCalledTimes(1);
  });
});
