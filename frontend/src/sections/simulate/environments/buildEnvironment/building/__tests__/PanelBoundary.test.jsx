import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import logger from "src/utils/logger";

import PanelBoundary from "../PanelBoundary";

vi.mock("src/utils/logger", () => ({
  default: { error: vi.fn(), warn: vi.fn() },
}));

const CRASH_MESSAGE = "stage_outputs.map is not a function";

let shouldThrow = false;

function Pane() {
  if (shouldThrow) throw new TypeError(CRASH_MESSAGE);
  return <div>Build pane</div>;
}

// W10-SIMULATE-OTHER-U06: a render error in the build pane must show a
// customer-facing fallback, not developer copy with a raw stack trace.
describe("PanelBoundary fallback", () => {
  let consoleSpy;

  beforeEach(() => {
    vi.clearAllMocks();
    shouldThrow = false;
    // React logs caught render errors to console.error; silence it.
    consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    consoleSpy.mockRestore();
  });

  it("tells the user what happened and what to do next, without the raw error", () => {
    shouldThrow = true;
    const { container } = render(
      <PanelBoundary>
        <Pane />
      </PanelBoundary>,
    );

    expect(screen.getByText(/this panel couldn.t load/i)).toBeInTheDocument();
    expect(
      screen.getByText(/the chat on the left still works\. reload the panel, or refresh the page if it keeps happening\./i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/right-side panel crashed/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/paste it back to me/i)).not.toBeInTheDocument();
    // Neither the error message nor its stack (which starts with the message) is printed.
    expect(container.textContent).not.toContain(CRASH_MESSAGE);
    expect(container.textContent).not.toMatch(/TypeError/);
    expect(container.querySelector("pre")).toBeNull();
  });

  it("offers an enabled Reload panel button that brings the pane back", () => {
    shouldThrow = true;
    render(
      <PanelBoundary>
        <Pane />
      </PanelBoundary>,
    );

    const reload = screen.getByRole("button", { name: "Reload panel" });
    expect(reload).toBeEnabled();

    shouldThrow = false;
    fireEvent.click(reload);

    expect(screen.getByText("Build pane")).toBeInTheDocument();
    expect(screen.queryByText(/this panel couldn.t load/i)).not.toBeInTheDocument();
  });

  it("sends the error to the error reporter instead of printing it", () => {
    shouldThrow = true;
    render(
      <PanelBoundary>
        <Pane />
      </PanelBoundary>,
    );

    expect(logger.error).toHaveBeenCalledTimes(1);
    const [, reported] = logger.error.mock.calls[0];
    expect(reported).toBeInstanceOf(TypeError);
    expect(reported.message).toBe(CRASH_MESSAGE);
  });
});
