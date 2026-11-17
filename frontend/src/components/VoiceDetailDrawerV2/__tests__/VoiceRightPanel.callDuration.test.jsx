import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import VoiceRightPanel from "../VoiceRightPanel";

// The walk's OTLP call: the transcript says who said what, with no per-turn
// timing, so nothing in it gives the length of the call.
const untimedTranscript = [
  {
    speaker_role: "assistant",
    content: "Hi, thanks for calling Acme support. How can I help?",
  },
  { speaker_role: "user", content: "I want to check my order status." },
  { speaker_role: "assistant", content: "Your order A-1001 has shipped." },
];

// Timed turns whose last utterance ends 38 s into the call.
const timedTranscript = [
  {
    speaker_role: "assistant",
    content: "Hello there",
    time: "2026-11-09T12:00:01.000Z",
    duration: 3,
  },
  {
    speaker_role: "user",
    content: "Where is my order?",
    time: "2026-11-09T12:00:30.000Z",
    duration: 8,
  },
];

const makeCall = (fields = {}) => ({
  id: "walk-call-none-1",
  module: "project",
  status: "completed",
  provider: "vapi",
  started_at: "2026-11-09T12:00:00.000Z",
  transcript: untimedTranscript,
  ...fields,
});

const renderCall = (call) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <VoiceRightPanel data={call} />
    </QueryClientProvider>,
  );
};

// The Duration cell of the Call Analytics strip (label + value). The header
// chip reads "Duration : 42s" as one text, so it never matches "Duration".
const analyticsDuration = () => screen.getByText("Duration").parentElement;

describe("OBS-SW-129 Call Analytics duration matches the call", () => {
  it.each(["project", "simulate"])(
    "shows the call's 42 s, as the header does, when the %s transcript has no timing",
    (module) => {
      renderCall(makeCall({ module, duration_seconds: 42 }));

      expect(screen.getByText("Duration : 42s")).toBeInTheDocument();
      expect(analyticsDuration()).toHaveTextContent(/^Duration42(\.0)?s$/);
    },
  );

  it("reads the call's duration field when there is no duration_seconds", () => {
    renderCall(makeCall({ duration: 42 }));

    expect(screen.getByText("Duration : 42s")).toBeInTheDocument();
    expect(analyticsDuration()).toHaveTextContent(/^Duration42(\.0)?s$/);
  });

  it("gives the call's length, not where the last utterance ends", () => {
    renderCall(makeCall({ duration_seconds: 42, transcript: timedTranscript }));

    expect(screen.getByText("Duration : 42s")).toBeInTheDocument();
    expect(analyticsDuration()).toHaveTextContent(/^Duration42(\.0)?s$/);
  });

  it("still uses the transcript when the call has no recorded length", () => {
    renderCall(makeCall({ transcript: timedTranscript }));

    expect(screen.queryByText(/^Duration : /)).not.toBeInTheDocument();
    expect(analyticsDuration()).toHaveTextContent(/^Duration38(\.0)?s$/);
  });

  it("shows a dash when neither the call nor its transcript has a length", () => {
    renderCall(makeCall());

    expect(analyticsDuration()).toHaveTextContent(/^Duration—$/);
  });
});
