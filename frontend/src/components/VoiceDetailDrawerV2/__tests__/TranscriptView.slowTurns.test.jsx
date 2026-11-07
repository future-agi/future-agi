import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import TranscriptView from "../TranscriptView";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

vi.mock("src/sections/common/CellMarkdown", () => ({
  default: ({ text }) => <div>{text}</div>,
}));

// The turn row is the box that holds the meta row and the content box:
// text div -> content box -> turn row.
const rowOf = (text) => screen.getByText(text).parentElement.parentElement;

const SLOW = /\bslow\b/i;

// Observe voice shape, straight from `voice_call_detail.transcript`:
// ISO `time` per turn, `duration` in seconds, no end field.
const at = (s) => new Date(Date.UTC(2026, 10, 2, 10, 0, 0) + s * 1000).toISOString();
const observeTranscript = [
  { id: "u1", speaker_role: "user", content: "Hi, I need to change my booking", time: at(0), duration: 2 },
  // agent answers 0.5s after the caller stops: normal
  { id: "a1", speaker_role: "assistant", content: "Sure, let me help with that", time: at(2.5), duration: 3 },
  { id: "u2", speaker_role: "user", content: "My reference is ABC123", time: at(6), duration: 2 },
  // agent answers 4.5s after the caller stops: slow
  { id: "a2", speaker_role: "assistant", content: "Thanks, I found your booking", time: at(12.5), duration: 3 },
  // caller waits 4.5s too, but caller turns are never slow
  { id: "u3", speaker_role: "user", content: "Great, thank you", time: at(20), duration: 1 },
];

describe("slow agent turns in the voice call transcript", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("highlights the agent turn that took 4.5s to answer, and only that one", () => {
    render(<TranscriptView transcript={observeTranscript} />);

    expect(rowOf("Thanks, I found your booking").textContent).toMatch(SLOW);

    expect(rowOf("Sure, let me help with that").textContent).not.toMatch(SLOW);
    expect(rowOf("Hi, I need to change my booking").textContent).not.toMatch(SLOW);
    expect(rowOf("My reference is ABC123").textContent).not.toMatch(SLOW);
    expect(rowOf("Great, thank you").textContent).not.toMatch(SLOW);

    // the existing silence divider is unchanged
    expect(screen.getAllByText("4.5s silence")).toHaveLength(2);
  });

  it("flags a slow agent turn in a simulate call and skips turns with no timing", () => {
    // Simulate voice shape: epoch-ms start/end under the `*TimeSeconds` names.
    const base = 1793646000000;
    const transcript = [
      { id: "u1", role: "user", content: "Where is my order", startTimeSeconds: base, endTimeSeconds: base + 1500 },
      { id: "a1", role: "assistant", content: "Checking the order status now", startTimeSeconds: base + 5500, endTimeSeconds: base + 7000 },
      { id: "u2", role: "user", content: "Okay", startTimeSeconds: base + 7400, endTimeSeconds: base + 8000 },
      { id: "a2", role: "assistant", content: "It ships tomorrow", startTimeSeconds: base + 8600, endTimeSeconds: base + 9600 },
      // no timing at all: renders, never flagged
      { id: "a3", role: "assistant", content: "Anything else I can do" },
    ];
    render(<TranscriptView transcript={transcript} />);

    expect(rowOf("Checking the order status now").textContent).toMatch(SLOW);
    expect(rowOf("It ships tomorrow").textContent).not.toMatch(SLOW);
    expect(rowOf("Anything else I can do").textContent).not.toMatch(SLOW);
    expect(rowOf("Where is my order").textContent).not.toMatch(SLOW);
  });

  it("flags nothing when every agent turn answers quickly", () => {
    const quick = observeTranscript.slice(0, 3);
    render(<TranscriptView transcript={quick} />);
    expect(screen.queryByText(SLOW)).toBeNull();
    expect(screen.getByText("Sure, let me help with that")).toBeInTheDocument();
  });
});
