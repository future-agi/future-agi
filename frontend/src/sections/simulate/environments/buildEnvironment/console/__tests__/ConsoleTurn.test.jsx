import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";

import { Turn } from "../ConsoleTurn";

const builder = (steps) => ({ id: "t1", role: "builder", steps });

describe("ConsoleTurn new step kinds", () => {
  it("renders a running tool row with an ellipsis and a completed one with its result", () => {
    render(<Turn turn={builder([
      { id: "s1", kind: "tool", label: "Read", state: "running" },
      { id: "s2", kind: "tool", label: "Write", state: "completed", result: "42 lines" },
    ])} />);
    expect(screen.getByText("Read")).toBeInTheDocument();
    expect(screen.getByText("…")).toBeInTheDocument();
    expect(screen.getByText("42 lines")).toBeInTheDocument();
  });

  it("renders an interrupted tool as finalized — its result, not the pulsing ellipsis", () => {
    // A tool that never finished (the run stopped): it must read as done, not as
    // a still-running (pulsing "…") row.
    render(<Turn turn={builder([
      { id: "s1", kind: "tool", label: "read run", state: "interrupted", result: "Didn't finish" },
    ])} />);
    expect(screen.getByText("read run")).toBeInTheDocument();
    expect(screen.getByText("Didn't finish")).toBeInTheDocument();
    expect(screen.queryByText("…")).toBeNull();
  });

  it("folds activity into a collapsible 'Run activity · N updates' group", () => {
    render(<Turn turn={builder([
      { id: "g1", kind: "group", count: 2, lines: ["scanning tests", "ALK moved to Build"] },
    ])} />);
    const header = screen.getByText("Run activity · 2 updates");
    expect(header).toBeInTheDocument();
    // collapsed by default
    expect(screen.queryByText("scanning tests")).toBeNull();
    fireEvent.click(header);
    expect(screen.getByText("scanning tests")).toBeInTheDocument();
    expect(screen.getByText("ALK moved to Build")).toBeInTheDocument();
  });

  it("renders error, cancelled and heartbeat steps", () => {
    render(<Turn turn={builder([
      { id: "e1", kind: "error", text: "It broke" },
      { id: "c1", kind: "cancelled", text: "Stopped." },
      { id: "h1", kind: "heartbeat" },
    ])} />);
    expect(screen.getByText("It broke")).toBeInTheDocument();
    expect(screen.getByText("Stopped.")).toBeInTheDocument();
    expect(screen.getByText(/working autonomously/i)).toBeInTheDocument();
  });

  it("renders a markdown note", () => {
    render(<Turn turn={builder([{ id: "n1", kind: "note", markdown: true, text: "**bold** move" }])} />);
    expect(screen.getByText("bold")).toBeInTheDocument();
  });

  it("wires an unresolved ask and shows a resolved summary from props", () => {
    const onSubmit = vi.fn();
    const question = { prompt: "How strict?", options: [{ label: "Strict" }], multiSelect: false, step: 1, total: 1 };
    const { rerender } = render(
      <Turn turn={builder([{ id: "a1", kind: "ask", question, onSubmit }])} />,
    );
    // Skip is hidden when no onSkip handler is provided (a blocking question).
    expect(screen.queryByRole("button", { name: "Skip" })).toBeNull();
    fireEvent.click(screen.getByText("Strict"));
    fireEvent.click(screen.getByRole("button", { name: "Submit" }));
    expect(onSubmit).toHaveBeenCalledWith(["Strict"]);

    rerender(
      <Turn turn={builder([{ id: "a1", kind: "ask", question, resolved: true, answerText: "Strict" }])} />,
    );
    // Resolved summary renders the answer verbatim and no Submit control.
    expect(screen.queryByRole("button", { name: "Submit" })).toBeNull();
    expect(screen.getAllByText("Strict").length).toBeGreaterThan(0);
  });
});
