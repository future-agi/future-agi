import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act } from "@testing-library/react";
import { render, screen, fireEvent } from "src/utils/test-utils";

// A controllable SpeechRecognition stub the tests can drive.
class MockSR {
  constructor() {
    MockSR.instance = this;
    this.lang = "";
    this.interimResults = false;
    this.continuous = false;
  }
  start() {
    this.started = true;
  }
  stop() {
    this.onend?.();
  }
}

async function loadVoiceInput({ supported }) {
  vi.resetModules();
  MockSR.instance = null;
  vi.stubGlobal("webkitSpeechRecognition", supported ? MockSR : undefined);
  vi.stubGlobal("SpeechRecognition", undefined);
  return (await import("../VoiceInput")).default;
}

describe("VoiceInput", () => {
  beforeEach(() => {
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("disables the mic when the browser has no Speech API", async () => {
    const VoiceInput = await loadVoiceInput({ supported: false });
    render(<VoiceInput onTranscript={vi.fn()} />);
    const btn = screen.getByRole("button", { name: "Speak instead of typing" });
    expect(btn).toBeDisabled();
  });

  it("transcribes into onTranscript while listening", async () => {
    const VoiceInput = await loadVoiceInput({ supported: true });
    const onTranscript = vi.fn();
    render(<VoiceInput onTranscript={onTranscript} />);

    fireEvent.click(screen.getByRole("button", { name: "Speak instead of typing" }));
    expect(MockSR.instance.started).toBe(true);
    expect(screen.getByText("listening")).toBeInTheDocument();

    // Fire a final result the way the Web Speech API shapes it.
    const results = [Object.assign([{ transcript: "add two scenarios" }], { isFinal: true })];
    act(() => MockSR.instance.onresult({ resultIndex: 0, results }));
    expect(onTranscript).toHaveBeenCalledWith("add two scenarios");
  });

  it("stops listening on a recognition error", async () => {
    const VoiceInput = await loadVoiceInput({ supported: true });
    render(<VoiceInput onTranscript={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Speak instead of typing" }));
    expect(screen.getByText("listening")).toBeInTheDocument();
    act(() => MockSR.instance.onerror({ error: "not-allowed" }));
    expect(screen.queryByText("listening")).toBeNull();
  });
});
