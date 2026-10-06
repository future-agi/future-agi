import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";

import SelectionBar from "../SelectionBar";

// Base handlers — each test overrides the ones it asserts on.
const base = () => ({
  count: 3,
  onClear: vi.fn(),
  onDelete: vi.fn(),
  onRun: vi.fn(),
  trials: 1,
  onTrialsChange: vi.fn(),
});

describe("SelectionBar", () => {
  it("shows the count pill and clears through the ✕", () => {
    const props = base();
    render(<SelectionBar {...props} />);

    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("scenarios selected")).toBeInTheDocument();

    // MUI applies the ✕'s aria-label as its accessible name.
    fireEvent.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(props.onClear).toHaveBeenCalledTimes(1);
  });

  it("wires Delete to its handler and shows no Edit button", () => {
    const props = base();
    render(<SelectionBar {...props} />);

    // Editing is per-row (the pencil drawer), not a bulk action — the bar
    // carries no Edit button.
    expect(screen.queryByText("Edit")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("Delete"));
    expect(props.onDelete).toHaveBeenCalledTimes(1);
  });

  it("runs the selection × the current k", () => {
    const props = { ...base(), trials: 1 };
    render(<SelectionBar {...props} />);

    fireEvent.click(screen.getByText(/Run simulation \(3\)/));
    expect(props.onRun).toHaveBeenCalledWith(1);
  });

  it("picks a repeats preset from the popover", () => {
    const props = base();
    render(<SelectionBar {...props} />);

    fireEvent.click(screen.getByText(/Repeats:/));
    // Preset rows read "1×", "3×", "5×", "8×".
    fireEvent.click(screen.getByText("5×"));
    expect(props.onTrialsChange).toHaveBeenCalledWith(5);
  });

  it("accepts a custom repeats value (clamped to 1–20)", () => {
    const props = base();
    render(<SelectionBar {...props} />);

    fireEvent.click(screen.getByText(/Repeats:/));
    fireEvent.click(screen.getByText("Custom…"));
    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "99" } });
    fireEvent.click(screen.getByText("Set"));
    expect(props.onTrialsChange).toHaveBeenCalledWith(20);
  });

  it("offers the select-all-matching escalation and calls onSelectAll", () => {
    const props = base();
    const onSelectAll = vi.fn();
    render(
      <SelectionBar
        {...props}
        matching={{ mode: "include", total: 60, pageCount: 25, onSelectAll }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Select all 60 matching/ }));
    expect(onSelectAll).toHaveBeenCalledTimes(1);
  });

  it("states the whole-match scope in all-mode and drops the escalation link", () => {
    const props = { ...base(), count: 60 };
    render(
      <SelectionBar
        {...props}
        matching={{ mode: "all", total: 60, pageCount: 25, onSelectAll: vi.fn() }}
      />,
    );

    expect(screen.getByText(/All 60 matching scenarios selected/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Select all/ })).not.toBeInTheDocument();
  });

  it("hides Repeats / Run when their handlers are absent", () => {
    render(
      <SelectionBar count={2} onClear={vi.fn()} onDelete={vi.fn()} />,
    );
    expect(screen.queryByText(/Repeats:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Run simulation/)).not.toBeInTheDocument();
    // Delete + Clear are always available.
    expect(screen.getByText("Delete")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear selection" })).toBeInTheDocument();
  });
});
