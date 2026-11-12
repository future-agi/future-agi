import { describe, expect, it } from "vitest";
import { computeCallMetrics, enrichTurns } from "../transcriptUtils";

const callStartedAt = "2026-11-09T12:00:00.000Z";
const epochMs = Date.parse(callStartedAt);
const isoTranscript = [
  {
    speaker_role: "assistant",
    content: "Hello there",
    time: "2026-11-09T12:00:05.000Z",
    duration: 2,
  },
  {
    speaker_role: "user",
    content: "A later reply",
    time: "2026-11-09T12:00:20.000Z",
    duration: 3,
  },
];

const expectCallOffsets = (turns) => {
  expect(turns.map(({ start }) => start)).toEqual([5, 20]);
  expect(turns.map(({ end }) => end)).toEqual([7, 23]);
  expect(turns[1].silenceBefore).toBe(13);
  expect(computeCallMetrics(turns)).toMatchObject({
    timeToFirstWord: 5,
    duration: 23,
  });
};

describe("OBS-SW-66 call-start transcript normalization", () => {
  it("preserves initial silence for absolute ISO timestamps", () => {
    expectCallOffsets(enrichTurns(isoTranscript, { callStartedAt }));
    expect(isoTranscript[0].time).toBe("2026-11-09T12:00:05.000Z");
  });

  it.each([
    ["ISO", callStartedAt],
    ["epoch milliseconds", epochMs],
    ["epoch seconds", epochMs / 1000],
    ["numeric string", String(epochMs / 1000)],
  ])("accepts a %s call-start anchor", (_, anchor) => {
    expectCallOffsets(enrichTurns(isoTranscript, { callStartedAt: anchor }));
  });

  it.each([
    ["milliseconds", 1000],
    ["seconds", 1],
  ])("anchors epoch %s starts and ends", (_, scale) => {
    const transcript = [
      {
        role: "assistant",
        startTimeSeconds: (epochMs / 1000 + 5) * scale,
        endTimeSeconds: (epochMs / 1000 + 7) * scale,
      },
      {
        role: "user",
        startTimeSeconds: (epochMs / 1000 + 20) * scale,
        endTimeSeconds: (epochMs / 1000 + 23) * scale,
      },
    ];
    expectCallOffsets(enrichTurns(transcript, { callStartedAt }));
  });

  it("keeps already-relative timestamps unchanged with an anchor", () => {
    expectCallOffsets(
      enrichTurns(
        [
          { role: "assistant", start: 5, end: 7 },
          { role: "user", start: 20, end: 23 },
        ],
        { callStartedAt },
      ),
    );
  });

  it("normalizes absolute speech alongside relative tool calls", () => {
    const turns = enrichTurns(
      [
        ...isoTranscript,
        { role: "tool", start: 10, end: 11, content: "Lookup" },
      ],
      { callStartedAt },
    );
    expect(turns.map(({ start }) => start)).toEqual([5, 10, 20]);
    expect(turns.map(({ end }) => end)).toEqual([7, 11, 23]);
    expect(turns[2].silenceBefore).toBe(13);
    expect(computeCallMetrics(turns).timeToFirstWord).toBe(5);
  });

  it.each([undefined, null, "not-a-date", "", 42])(
    "retains earliest-turn fallback for an unavailable anchor (%s)",
    (anchor) => {
      const turns = enrichTurns(isoTranscript, { callStartedAt: anchor });
      expect(turns.map(({ start }) => start)).toEqual([0, 15]);
      expect(turns.map(({ end }) => end)).toEqual([2, 18]);
    },
  );

  it("does not add audio timing to text-only transcripts", () => {
    const turns = enrichTurns(isoTranscript, {
      callStartedAt,
      enableTemporalFeatures: false,
    });
    expect(
      turns.every((turn) => turn.start === null && turn.end === null),
    ).toBe(true);
    expect(computeCallMetrics(turns).timeToFirstWord).toBeNull();
  });
});
