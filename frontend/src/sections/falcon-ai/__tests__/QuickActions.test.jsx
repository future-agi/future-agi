import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
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

const LABELS = [
  "Analyse my error feed",
  "Create an Imagine view",
  "Build a dataset",
  "Create an evaluation",
  "Run simulation for my agent",
];

// Click every chip and return the prompts it hands to the chat, in label order.
function clickAllChips() {
  const onAction = vi.fn();
  render(<QuickActions onAction={onAction} />);
  LABELS.forEach((label) => fireEvent.click(screen.getByText(label)));
  expect(onAction).toHaveBeenCalledTimes(LABELS.length);
  return onAction.mock.calls.map(([prompt]) => prompt);
}

describe("QuickActions", () => {
  beforeEach(() => {
    mode.prototype = false;
  });

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

  // A suggestion can be sent unedited (the Imagine panel sends on click), so
  // it must not carry blanks for the person to fill in.
  it("gives every chip a prompt with no blanks to fill in", () => {
    const prompts = clickAllChips();
    prompts.forEach((prompt) => {
      ["[", "]", "<", ">", "{", "}"].forEach((mark) =>
        expect(prompt).not.toContain(mark),
      );
    });
  });

  it("keeps every chip's prompt short enough to read before sending", () => {
    clickAllChips().forEach((prompt) =>
      expect(prompt.length).toBeLessThanOrEqual(300),
    );
  });

  // A new workspace may have no traces, agents or data, so each prompt tells
  // the assistant what to do in that case instead of failing on empty data.
  it("tells the assistant what to do when there is no data yet", () => {
    clickAllChips().forEach((prompt) =>
      expect(prompt).toMatch(/\bIf (I have no|I don't have|there are no)\b/),
    );
  });

  // Outside the trace drawer's Imagine tab the assistant cannot build a view
  // and has no other source for where Imagine lives, so the prompt names the
  // button, and that check comes before any questions.
  it("says where Imagine is before asking what to show", () => {
    const onAction = vi.fn();
    render(<QuickActions onAction={onAction} />);
    fireEvent.click(screen.getByText("Create an Imagine view"));
    expect(onAction).toHaveBeenCalledTimes(1);
    const sent = onAction.mock.calls[0][0];
    expect(sent).toMatch(/open a trace and click Imagine/);
    const lower = sent.toLowerCase();
    const cantBuild = lower.indexOf("can't build");
    const askMe = lower.indexOf("ask me");
    expect(cantBuild).toBeGreaterThan(-1);
    expect(askMe).toBeGreaterThan(cantBuild);
  });

  // Only the prototype-only comparison starter is removed, not real actions.
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
