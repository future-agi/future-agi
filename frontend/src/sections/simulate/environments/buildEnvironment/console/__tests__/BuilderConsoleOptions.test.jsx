import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";
import BuilderConsole from "../BuilderConsole";

describe("BuilderConsole while a question takes only its options", () => {
  it("holds free text and says to answer with the options above", () => {
    render(<BuilderConsole turns={[]} onSend={() => {}} answerWithOptions />);
    const box = screen.getByPlaceholderText("Answer with the options above");
    expect(box).toBeDisabled();
  });

  it("takes free text otherwise", () => {
    render(<BuilderConsole turns={[]} onSend={() => {}} />);
    expect(
      screen.queryByPlaceholderText("Answer with the options above"),
    ).toBeNull();
  });
});

describe("BuilderConsole paperclip", () => {
  it("shows a held paperclip disabled, with the hold as its tooltip", () => {
    render(<BuilderConsole turns={[]} onSend={() => {}} attachHold="R" />);
    expect(
      screen.getByRole("button", { name: "Attach a dataset, CSV or file" }),
    ).toBeDisabled();
    expect(screen.getByLabelText("R")).toBeInTheDocument();
  });
});
