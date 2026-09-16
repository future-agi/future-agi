import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";

import BuilderConsole from "../console/BuilderConsole";
import { CONSOLE_COPY } from "../build.constants";

beforeAll(() => {
  // jsdom has no layout; the console auto-scrolls to the latest turn.
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});

const builderTurn = {
  id: "t1",
  role: "builder",
  title: "Understanding the agent",
  steps: [
    { kind: "think", text: "Let me read the repository" },
    { kind: "tool", label: "list_files", result: "42 files scanned" },
    { kind: "file", path: "contract.json", note: "wrote the contract" },
    { kind: "json", label: "issue_refund", value: "seeded 99 rows" },
    { kind: "note", text: "a plain note" },
  ],
};

const userTurn = { id: "u1", role: "user", text: "make it harder" };

describe("BuilderConsole", () => {
  it("shows the idle header and empty state when there are no turns", () => {
    render(<BuilderConsole turns={[]} running={false} />);
    expect(screen.getByText(CONSOLE_COPY.idle)).toBeInTheDocument();
    expect(screen.getByText(CONSOLE_COPY.empty)).toBeInTheDocument();
  });

  it("shows the working header and the working row while running", () => {
    render(<BuilderConsole turns={[]} running />);
    expect(screen.getByText(CONSOLE_COPY.working)).toBeInTheDocument();
    expect(screen.getByText(CONSOLE_COPY.workingDot)).toBeInTheDocument();
  });

  it("renders a builder turn: title, prose, tool, file and expandable json", () => {
    render(<BuilderConsole turns={[builderTurn]} running={false} />);

    const title = screen.getByText("Understanding the agent");
    expect(title).toBeInTheDocument();
    expect(title).toHaveStyle({ textTransform: "uppercase" });

    expect(screen.getByText("Let me read the repository")).toBeInTheDocument();
    expect(screen.getByText("a plain note")).toBeInTheDocument();

    expect(screen.getByText("list_files")).toBeInTheDocument();
    expect(screen.getByText("42 files scanned")).toBeInTheDocument();

    expect(screen.getByText("contract.json")).toBeInTheDocument();
    expect(screen.getByText("wrote the contract")).toBeInTheDocument();

    // json row starts collapsed; its value only shows after a click.
    expect(screen.getByText("issue_refund")).toBeInTheDocument();
    expect(screen.queryByText("seeded 99 rows")).toBeNull();
    fireEvent.click(screen.getByText("issue_refund"));
    expect(screen.getByText("seeded 99 rows")).toBeInTheDocument();
  });

  it("renders a user turn in the bubble", () => {
    render(<BuilderConsole turns={[userTurn]} running={false} />);
    expect(screen.getByText("make it harder")).toBeInTheDocument();
  });

  it("renders chips as buttons only when idle and fires onChip", () => {
    const onChip = vi.fn();
    const { rerender } = render(
      <BuilderConsole turns={[]} running={false} chips={["Show tools"]} onChip={onChip} />,
    );
    const chip = screen.getByRole("button", { name: "Show tools" });
    fireEvent.click(chip);
    expect(onChip).toHaveBeenCalledWith("Show tools");

    rerender(<BuilderConsole turns={[]} running chips={["Show tools"]} onChip={onChip} />);
    expect(screen.queryByRole("button", { name: "Show tools" })).toBeNull();
  });

  it("sends the draft on Enter and clears the field", () => {
    const onSend = vi.fn();
    render(<BuilderConsole turns={[]} running={false} onSend={onSend} />);
    const field = screen.getByPlaceholderText(CONSOLE_COPY.placeholder);
    fireEvent.change(field, { target: { value: "hello" } });
    fireEvent.keyDown(field, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("hello", []);
    expect(field).toHaveValue("");
  });

  it("does not send on Shift+Enter", () => {
    const onSend = vi.fn();
    render(<BuilderConsole turns={[]} running={false} onSend={onSend} />);
    const field = screen.getByPlaceholderText(CONSOLE_COPY.placeholder);
    fireEvent.change(field, { target: { value: "world" } });
    fireEvent.keyDown(field, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("disables the send button on an empty draft", () => {
    render(<BuilderConsole turns={[]} running={false} onSend={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("attaches a file via the hidden input and can remove it", () => {
    const { container } = render(<BuilderConsole turns={[]} running={false} onSend={vi.fn()} />);
    const input = container.querySelector('input[type="file"]');
    const file = new File(["a".repeat(2048)], "data.csv", { type: "text/csv" });
    fireEvent.change(input, { target: { files: [file] } });

    expect(screen.getByText("data.csv")).toBeInTheDocument();
    expect(screen.getByText("2 kB")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Remove data.csv" }));
    expect(screen.queryByText("data.csv")).toBeNull();
  });
});
