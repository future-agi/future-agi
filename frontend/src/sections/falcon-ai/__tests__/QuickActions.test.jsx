import PropTypes from "prop-types";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import QuickActions from "../components/QuickActions";

// Mock Iconify
function MockIconify({ icon, ...props }) {
  return <span data-testid="iconify" data-icon={icon} {...props} />;
}

MockIconify.propTypes = {
  icon: PropTypes.string.isRequired,
};

vi.mock("src/components/iconify", () => ({
  default: MockIconify,
}));

// The real app keeps its five chips; prototype starters are removed by 749.
const mode = vi.hoisted(() => ({ prototype: false }));
vi.mock("src/utils/prototype", () => ({
  isPrototypeMode: () => mode.prototype,
}));

describe("QuickActions", () => {
  it("renders 5 quick action chips", () => {
    render(<QuickActions onAction={vi.fn()} />);
    const chips = screen.getAllByRole("button");
    expect(chips).toHaveLength(5);
  });

  it("renders expected action labels", () => {
    render(<QuickActions onAction={vi.fn()} />);
    expect(screen.getByText("Analyse my error feed")).toBeInTheDocument();
    expect(screen.getByText("Build a dataset")).toBeInTheDocument();
    expect(screen.getByText("Create an evaluation")).toBeInTheDocument();
    expect(screen.getByText("Create an Imagine view")).toBeInTheDocument();
    expect(screen.getByText("Run simulation for my agent")).toBeInTheDocument();
  });

  it("calls onAction with the chip's full prompt when clicked", () => {
    const onAction = vi.fn();
    render(<QuickActions onAction={onAction} />);
    fireEvent.click(screen.getByText("Build a dataset"));
    expect(onAction).toHaveBeenCalledTimes(1);
    const sent = onAction.mock.calls[0][0];
    expect(sent).toMatch(/Build a dataset from my traces/i);
    expect(sent.length).toBeGreaterThan(80);
  });

  // Ruling 749 removes the prototype-only comparison starter, not real actions.
  it("shows no quick actions in prototype mode (749)", () => {
    mode.prototype = true;
    const onAction = vi.fn();
    render(<QuickActions onAction={onAction} />);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    [
      "Analyse my error feed",
      "Create an Imagine view",
      "Build a dataset",
      "Create an evaluation",
      "Run simulation for my agent",
    ].forEach((label) => expect(screen.queryByText(label)).toBeNull());
    expect(
      screen.queryByText("Is my latest agent better than the previous one?"),
    ).toBeNull();
    expect(onAction).not.toHaveBeenCalled();
    mode.prototype = false;
  });

  it("handles missing onAction prop gracefully", () => {
    render(<QuickActions />);
    // Clicking should not throw
    expect(() => {
      fireEvent.click(screen.getByText("Build a dataset"));
    }).not.toThrow();
  });
});
