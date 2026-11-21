import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";
import BuilderConsole from "../BuilderConsole";

const PICK_OR_TYPE = "Pick an option above, or type your own answer";

describe("BuilderConsole while a question takes only its options", () => {
  it("holds free text and says to answer with the options above", () => {
    render(<BuilderConsole turns={[]} onSend={() => {}} answerWithOptions />);
    const box = screen.getByPlaceholderText("Answer with the options above");
    expect(box).toBeDisabled();
  });

  const asking = (over = {}) => [
    {
      id: "t1",
      role: "builder",
      steps: [
        {
          id: "q1",
          kind: "ask",
          open: true,
          resolved: false,
          question: {
            prompt: "How would you like to restore it?",
            options: [{ label: "As the same case" }, { label: "As a new case" }],
            optionsOnly: false,
            step: 1,
            total: 1,
          },
          ...over,
        },
      ],
    },
  ];

  it("points at the options of an open question that also takes the person's own answer, and stays usable", () => {
    render(<BuilderConsole turns={asking()} onSend={() => {}} />);
    expect(screen.getByPlaceholderText(PICK_OR_TYPE)).toBeEnabled();
    expect(screen.queryByPlaceholderText("Reply to the builder…")).toBeNull();
  });

  it.each([
    ["answered", { resolved: true }],
    ["no longer the open one", { open: false }],
    ["without options", { question: { prompt: "Why?", options: [], step: 1, total: 1 } }],
  ])("keeps the usual placeholder for a question %s", (_name, over) => {
    render(<BuilderConsole turns={asking(over)} onSend={() => {}} />);
    expect(screen.queryByPlaceholderText(PICK_OR_TYPE)).toBeNull();
  });

  it("takes free text otherwise", () => {
    render(<BuilderConsole turns={[]} onSend={() => {}} />);
    expect(
      screen.queryByPlaceholderText("Answer with the options above"),
    ).toBeNull();
  });
});
