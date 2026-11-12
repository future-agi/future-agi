import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import VoiceLeftPanel from "../VoiceLeftPanel";

// The transcript list has its own tests; here it only has to appear or not.
vi.mock("../TranscriptView", () => ({
  default: () => <div data-testid="transcript-view" />,
}));

const NO_TRANSCRIPT = "No transcript for this call";

// An unanswered Observe call: nothing was recorded and nothing was said.
const unansweredCall = {
  id: "call-unanswered",
  module: "project",
  status: "completed",
  recording_available: false,
  ended_reason: "customer-did-not-answer",
  transcript: [],
};

describe("OBS-SW-112 Observe call with no recording and no transcript", () => {
  it("says both are missing, with the transcript message under the recording one", () => {
    render(<VoiceLeftPanel data={unansweredCall} />);

    const noRecording = screen.getByText("No recording for this call");
    expect(
      screen.getByText("Call ended: customer-did-not-answer"),
    ).toBeInTheDocument();
    const noTranscript = screen.getByText(NO_TRANSCRIPT);
    expect(
      noRecording.compareDocumentPosition(noTranscript) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.queryByTestId("transcript-view")).not.toBeInTheDocument();
  });

  it.each([
    ["missing", undefined],
    [
      "only system turns",
      [{ speaker_role: "system", content: "You are a support agent." }],
    ],
  ])("treats a transcript that is %s as no transcript", (_, transcript) => {
    render(<VoiceLeftPanel data={{ ...unansweredCall, transcript }} />);

    expect(screen.getByText(NO_TRANSCRIPT)).toBeInTheDocument();
  });

  it("shows the transcript and no message when the call has one", () => {
    render(
      <VoiceLeftPanel
        data={{
          ...unansweredCall,
          transcript: [{ speaker_role: "user", content: "Hello?" }],
        }}
      />,
    );

    expect(screen.getByTestId("transcript-view")).toBeInTheDocument();
    expect(screen.queryByText(NO_TRANSCRIPT)).not.toBeInTheDocument();
  });

  it("does not say a simulated call has no transcript while its detail loads", () => {
    // Simulate list rows arrive without a transcript; the per-call detail
    // fills it in a moment later.
    render(
      <VoiceLeftPanel
        data={{
          id: "sim-call",
          module: "simulate",
          status: "completed",
          transcript: [],
        }}
      />,
    );

    expect(screen.queryByText(NO_TRANSCRIPT)).not.toBeInTheDocument();
  });
});
