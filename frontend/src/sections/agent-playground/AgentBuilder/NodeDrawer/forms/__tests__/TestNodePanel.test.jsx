import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import TestNodePanel from "../TestNodePanel";

describe("TestNodePanel", () => {
  it("stays collapsed until the toggle is clicked", () => {
    render(<TestNodePanel messages={[]} onRunTest={vi.fn()} />);

    expect(screen.queryByText("Run test")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("Test this node"));

    expect(screen.getByText("Run test")).toBeInTheDocument();
  });

  it("renders a sample-value field for each {{variable}} in the messages", () => {
    const messages = [
      {
        role: "user",
        content: [{ type: "text", text: "Say {{greeting}} to {{name}}" }],
      },
    ];
    render(<TestNodePanel messages={messages} onRunTest={vi.fn()} />);

    fireEvent.click(screen.getByText("Test this node"));

    expect(screen.getByLabelText("greeting")).toBeInTheDocument();
    expect(screen.getByLabelText("name")).toBeInTheDocument();
  });

  it("calls onRunTest with the entered sample values and shows a success result", async () => {
    const onRunTest = vi.fn().mockResolvedValue({
      status: "SUCCESS",
      outputs: { response: "Hi World!" },
      error: null,
    });
    const messages = [
      { role: "user", content: [{ type: "text", text: "Say hi to {{name}}" }] },
    ];
    render(<TestNodePanel messages={messages} onRunTest={onRunTest} />);

    fireEvent.click(screen.getByText("Test this node"));
    fireEvent.change(screen.getByLabelText("name"), {
      target: { value: "World" },
    });
    fireEvent.click(screen.getByText("Run test"));

    await waitFor(() => {
      expect(onRunTest).toHaveBeenCalledWith({ name: "World" });
    });
    expect(await screen.findByText("Hi World!")).toBeInTheDocument();
  });

  it("shows the error message when the test run fails", async () => {
    const onRunTest = vi.fn().mockResolvedValue({
      status: "FAILED",
      outputs: {},
      error: "PromptVersion configuration missing 'model'",
    });
    render(<TestNodePanel messages={[]} onRunTest={onRunTest} />);

    fireEvent.click(screen.getByText("Test this node"));
    fireEvent.click(screen.getByText("Run test"));

    expect(
      await screen.findByText("PromptVersion configuration missing 'model'"),
    ).toBeInTheDocument();
  });

  it("does not expand when disabled", () => {
    render(<TestNodePanel messages={[]} onRunTest={vi.fn()} disabled />);

    fireEvent.click(screen.getByText("Test this node"));

    expect(screen.queryByText("Run test")).not.toBeInTheDocument();
  });
});
