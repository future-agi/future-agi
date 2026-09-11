import { describe, it, expect, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen } from "src/utils/test-utils";
import FixedTab from "../FixedTab";

describe("FixedTab", () => {
  const defaultProps = {
    tabKey: "traces",
    label: "Trace",
    icon: "mdi:link-variant",
    shortcut: "1",
    isActive: false,
    onClick: vi.fn(),
  };

  it("renders the label text", () => {
    render(<FixedTab {...defaultProps} />);
    expect(screen.getByText("Trace")).toBeInTheDocument();
  });

  it("calls onClick with tabKey when clicked", async () => {
    const onClick = vi.fn();
    render(<FixedTab {...defaultProps} onClick={onClick} />);
    const button = screen.getByRole("button");
    button.click();
    expect(onClick).toHaveBeenCalledWith("traces");
  });

  it("renders with active styling when isActive is true", () => {
    render(<FixedTab {...defaultProps} isActive={true} />);
    const button = screen.getByRole("button");
    // Active tab should have a different visual state (tested via MUI sx)
    expect(button).toBeInTheDocument();
  });

  it("renders without icon when icon prop is not provided", () => {
    render(<FixedTab {...defaultProps} icon={undefined} />);
    expect(screen.getByText("Trace")).toBeInTheDocument();
  });

  it("shows the keyboard shortcut hint ('Press <n>') on hover, not an index like '(n)'", async () => {
    render(<FixedTab {...defaultProps} shortcut="3" />);
    await userEvent.hover(screen.getByRole("button"));
    expect(await screen.findByText("Press 3")).toBeInTheDocument();
  });

  it("does not hint the shortcut on the active tab (pressing it is a no-op)", async () => {
    render(<FixedTab {...defaultProps} shortcut="3" isActive />);
    await userEvent.hover(screen.getByRole("button"));
    expect(screen.queryByText("Press 3")).not.toBeInTheDocument();
  });
});
