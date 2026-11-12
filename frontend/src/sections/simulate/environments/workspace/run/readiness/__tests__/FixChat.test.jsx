import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";

const useWorkspaceChat = vi.fn();
vi.mock("src/api/simulate-environments/workspaceChat", () => ({
  useWorkspaceChat: (...args) => useWorkspaceChat(...args),
}));

import FixChat from "../FixChat";

beforeAll(() => {
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});

const chatWith = (over = {}) => ({
  turns: [{ id: "u1", role: "user", text: "point it at the test world" }],
  running: false,
  send: vi.fn(),
  stop: vi.fn(),
  canStop: false,
  working: false,
  stopping: false,
  frozen: false,
  frozenReason: "",
  ...over,
});

describe("FixChat", () => {
  beforeEach(() => useWorkspaceChat.mockReset());

  it("shows the fix's turns only and sends typed text without the row selection", () => {
    const chat = chatWith({
      turns: [
        { id: "old", role: "user", text: "an earlier message" },
        { id: "u1", role: "user", text: "point it at the test world" },
      ],
    });
    useWorkspaceChat.mockReturnValue(chat);
    const env = { id: "e1" };
    render(<FixChat env={env} fixRun={{ message_id: "u1" }} />);

    expect(screen.getByText("point it at the test world")).toBeInTheDocument();
    expect(screen.queryByText("an earlier message")).toBeNull();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "go" } });
    fireEvent.keyDown(screen.getByRole("textbox"), {
      key: "Enter",
      code: "Enter",
    });

    expect(chat.send).toHaveBeenCalledWith("go");
    expect(useWorkspaceChat).toHaveBeenCalledWith(env, {
      source: "harness",
      scoped: false,
    });
  });

  it("shows lines added to the fix as queued until the builder takes them", () => {
    useWorkspaceChat.mockReturnValue(chatWith());
    render(
      <FixChat
        env={{ id: "e1" }}
        fixRun={{
          message_id: "u1",
          queue: [{ message: "Reset the orders store", rows: ["r"] }],
        }}
        lines={["Tools wired to the test world", "World resets"]}
      />,
    );
    expect(screen.getByText("Reset the orders store")).toBeInTheDocument();
    expect(screen.getByText("Queued")).toBeInTheDocument();
    expect(
      screen.getByText(/Fixing: Tools wired to the test world, World resets/),
    ).toBeInTheDocument();
  });

  it("shows why a frozen chat can't take messages", () => {
    useWorkspaceChat.mockReturnValue(
      chatWith({ frozen: true, frozenReason: "No workspace yet" }),
    );
    render(<FixChat env={{ id: "e1" }} />);

    expect(screen.getByRole("textbox")).toHaveAttribute(
      "placeholder",
      "No workspace yet",
    );
    expect(screen.getByRole("textbox")).toBeDisabled();
  });
});
