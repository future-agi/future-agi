import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import PanelHostedPlatform from "../panels/PanelHostedPlatform";

describe("PanelHostedPlatform", () => {
  it("renders five agent-type chips, three coming soon", () => {
    render(<PanelHostedPlatform onBuild={vi.fn()} />);
    expect(screen.getByText("Voice")).toBeInTheDocument();
    expect(screen.getByText("Chat")).toBeInTheDocument();
    expect(screen.getByText("Computer use")).toBeInTheDocument();
    expect(screen.getByText("Robotics")).toBeInTheDocument();
    // Three agent types, plus LiveKit in the voice roster.
    expect(screen.getAllByLabelText("Coming soon")).toHaveLength(4);
  });

  it("keeps Voice selected when a coming-soon type is clicked", () => {
    render(<PanelHostedPlatform onBuild={vi.fn()} />);
    fireEvent.click(screen.getByText("Code"));
    // Voice roster still shown → Vapi mark present.
    expect(screen.getByLabelText("Vapi")).toBeInTheDocument();
  });

  it("shows the voice roster with wordmarks and named marks", () => {
    render(<PanelHostedPlatform onBuild={vi.fn()} />);
    // Vapi + Retell are wordmark logos → no text label.
    expect(screen.queryByText("Vapi")).toBeNull();
    expect(screen.queryByText("Retell AI")).toBeNull();
    expect(screen.getByLabelText("Vapi")).toBeInTheDocument();
    expect(screen.getByLabelText("Retell AI")).toBeInTheDocument();
    // Bland / ElevenLabs / LiveKit show their names.
    expect(screen.getByText("Bland.ai")).toBeInTheDocument();
    expect(screen.getByText("ElevenLabs")).toBeInTheDocument();
    expect(screen.getByText("LiveKit")).toBeInTheDocument();
  });

  it("cannot select LiveKit — the backend rejects connect_only for it today", () => {
    render(<PanelHostedPlatform onBuild={vi.fn()} />);

    const livekit = screen.getByText("LiveKit").closest("[role='button']");
    expect(livekit).toHaveAttribute("aria-disabled", "true");

    fireEvent.click(screen.getByText("LiveKit"));
    // Still on Vapi — its assistant-id placeholder, not LiveKit's agent name.
    expect(screen.getByPlaceholderText("asst_9f2c…")).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("returns-line-agent")).toBeNull();
  });

  it("switches to the chat roster and clears the id/key fields", () => {
    render(<PanelHostedPlatform onBuild={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText("asst_9f2c…"), {
      target: { value: "asst_x" },
    });
    fireEvent.change(screen.getByPlaceholderText("sk-…"), {
      target: { value: "sk-x" },
    });
    fireEvent.click(screen.getByText("Chat"));

    expect(screen.getByText("OpenAI Assistants")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("asst_9f2c…").value).toBe("");
    expect(screen.getByPlaceholderText("sk-…").value).toBe("");
  });

  it("only shows call direction for voice", () => {
    render(<PanelHostedPlatform onBuild={vi.fn()} />);
    expect(screen.getByText("Call direction")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Chat"));
    expect(screen.queryByText("Call direction")).toBeNull();
  });

  it("gates the CTA until both credential fields are filled", () => {
    const onBuild = vi.fn();
    render(<PanelHostedPlatform onBuild={onBuild} />);
    expect(screen.getByText("Fill both fields")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Build environment/ })).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText("asst_9f2c…"), {
      target: { value: "asst_x" },
    });
    fireEvent.change(screen.getByPlaceholderText("sk-…"), {
      target: { value: "sk-x" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Build environment/ }));
    expect(onBuild).toHaveBeenCalledWith({
      kind: "platform",
      agentType: "voice",
      provider: "vapi",
      agentId: "asst_x",
      apiKey: "sk-x",
      callDirection: "inbound",
    });
  });
});
