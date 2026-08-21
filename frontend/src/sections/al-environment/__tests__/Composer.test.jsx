import { describe, it, expect, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import Composer from "../Composer";

const props = {
  onSay: () => {},
  onRun: () => {},
  onStop: () => {},
  streaming: false,
  status: { session: { id: "s1" }, agent: "drive_thru", stage: "understand", have: {}, busy: false },
  sessionId: "session-0b718f",
  artifactsPath: "/Users/someone/agent-learning-kit/artifacts/sessions/session-0b718f",
};

describe("Composer", () => {
  it("sends what was typed", async () => {
    const onSay = vi.fn();
    render(<Composer {...props} onSay={onSay} />);
    await userEvent.type(screen.getByRole("textbox"), "build the world");
    await userEvent.click(screen.getByRole("button", { name: /^send$/i }));
    expect(onSay).toHaveBeenCalledWith("build the world");
  });

  it("clears the box after sending", async () => {
    render(<Composer {...props} onSay={vi.fn()} />);
    const box = screen.getByRole("textbox");
    await userEvent.type(box, "hello");
    await userEvent.click(screen.getByRole("button", { name: /^send$/i }));
    expect(box).toHaveValue("");
  });

  it("refuses to send nothing", async () => {
    render(<Composer {...props} />);
    await userEvent.type(screen.getByRole("textbox"), "   ");
    expect(screen.getByRole("button", { name: /^send$/i })).toBeDisabled();
  });

  it("advertises the focus shortcut in the placeholder", () => {
    render(<Composer {...props} />);
    expect(screen.getByPlaceholderText(/\( \/ to focus \)/)).toBeInTheDocument();
  });

  it("focuses itself when / is pressed elsewhere on the page", async () => {
    render(<Composer {...props} />);
    const box = screen.getByRole("textbox");
    expect(box).not.toHaveFocus();
    await userEvent.keyboard("/");
    expect(box).toHaveFocus();
  });

  it("advances the stage by sending an empty message", async () => {
    const onSay = vi.fn();
    render(<Composer {...props} onSay={onSay} />);
    await userEvent.click(screen.getByRole("button", { name: /next stage/i }));
    expect(onSay).toHaveBeenCalledWith("");
  });

  it("runs every scenario from a chip", async () => {
    const onRun = vi.fn();
    render(
      <Composer
        {...props}
        onRun={onRun}
        status={{ ...props.status, stage: "scenarios", have: { world: true, scenarios: 3 } }}
      />
    );
    await userEvent.click(screen.getByRole("button", { name: /run all 3/i }));
    expect(onRun).toHaveBeenCalledWith("");
  });

  it("keeps Send visible beside Stop while a turn runs", async () => {
    const onStop = vi.fn();
    render(<Composer {...props} streaming onStop={onStop} />);
    expect(screen.getByRole("button", { name: "…" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: /stop/i }));
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it("names the session and where its artifacts live", () => {
    render(<Composer {...props} />);
    expect(screen.getByText(/session-0b718f/)).toBeInTheDocument();
    // The machine-specific root is dropped; the part that names the folder stays.
    expect(screen.getByText(/artifacts\/sessions\/session-0b718f/)).toBeInTheDocument();
    expect(screen.queryByText(/\/Users\/someone/)).not.toBeInTheDocument();
  });

  it("says there is no session when there is none", () => {
    render(<Composer {...props} status={{ busy: false }} sessionId={null} />);
    expect(screen.getByText("no session")).toBeInTheDocument();
  });

  it("sends on Enter but allows a newline with Shift", async () => {
    const onSay = vi.fn();
    render(<Composer {...props} onSay={onSay} />);
    const box = screen.getByRole("textbox");
    await userEvent.type(box, "first{Shift>}{Enter}{/Shift}second");
    expect(onSay).not.toHaveBeenCalled();
    await userEvent.type(box, "{Enter}");
    expect(onSay).toHaveBeenCalledWith("first\nsecond");
  });
});
