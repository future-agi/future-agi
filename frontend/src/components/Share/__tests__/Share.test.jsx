import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import Share from "../Share";

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/utils/utils", () => ({
  copyToClipboard: vi.fn(),
}));

vi.mock("src/utils/Mixpanel", () => ({
  Events: { pExperimentProjectShared: "pExperimentProjectShared" },
  trackEvent: vi.fn(),
}));

vi.mock("src/components/iconify", () => ({
  __esModule: true,
  default: ({ icon }) => <span data-testid={`iconify-${icon}`} />,
}));

const defaultProps = {
  open: true,
  onClose: vi.fn(),
  title: "Share as link",
  body: "Share this link to give others access to the selected runs.",
};

describe("Share", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // Regression for #1501: misleading "Done" button should now be "Close"
  it("renders the confirmation button with text 'Close' instead of 'Done'", () => {
    render(<Share {...defaultProps} />);
    expect(screen.getByText("Close")).toBeInTheDocument();
    expect(screen.queryByText("Done")).toBeNull();
  });

  it("uses aria-label 'close-share-project' on the confirmation button", () => {
    render(<Share {...defaultProps} />);
    expect(
      screen.getByRole("button", { name: /close-share-project/i }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /finish-share-project/i }),
    ).toBeNull();
  });

  it("renders the confirmation button as variant='outlined' (not 'contained')", () => {
    render(<Share {...defaultProps} />);
    const closeBtn = screen.getByRole("button", {
      name: /close-share-project/i,
    });
    expect(closeBtn).toHaveClass("MuiButton-outlined");
    expect(closeBtn).not.toHaveClass("MuiButton-contained");
  });

  // Acceptance criterion: dialog description clarifies no access control
  it("renders a clarification note that the link has no access control", () => {
    render(<Share {...defaultProps} />);
    expect(screen.getByText(/without access control/i)).toBeInTheDocument();
  });

  // Preserves the body prop API (callers can still pass custom body text)
  it("renders the body prop text passed by the caller", () => {
    const customBody = "Custom body text from caller";
    render(<Share {...defaultProps} body={customBody} />);
    expect(screen.getByText(customBody)).toBeInTheDocument();
  });

  // Regression: Cancel button must remain unchanged
  it("renders the Cancel button unchanged (outlined, aria-label 'Cancel-share-project')", () => {
    render(<Share {...defaultProps} />);
    const cancelBtn = screen.getByRole("button", {
      name: /Cancel-share-project/i,
    });
    expect(cancelBtn).toBeInTheDocument();
    expect(cancelBtn).toHaveTextContent("Cancel");
    expect(cancelBtn).toHaveClass("MuiButton-outlined");
  });

  it("calls onClose exactly once when the Close button is clicked", () => {
    const onClose = vi.fn();
    render(<Share {...defaultProps} onClose={onClose} />);
    fireEvent.click(
      screen.getByRole("button", { name: /close-share-project/i }),
    );
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  // Edge case: body is optional — should not crash when omitted
  it("renders without crashing when body prop is omitted", () => {
    const { container } = render(<Share open={true} onClose={vi.fn()} />);
    expect(container).toBeDefined();
    // clarification note should still render
    expect(screen.getByText(/without access control/i)).toBeInTheDocument();
  });
});
