import { describe, expect, it } from "vitest";
import {
  formatUsd,
  sdkStatus,
  streamRowText,
  streamedResults,
  uploadStatus,
} from "../runFromCodeModel";

const HS =
  "Connected · alice@mbp-14 · git 3fa91c2 (dirty: agent.py +14 −2) · Python 3.12 · futureagi 0.9.1 (Preview)";
const result = (row, trial, passed, at = "2026-10-15T10:00:00Z") => ({
  event: "result",
  row_id: `r${row}`,
  row_label: `T${row}`,
  trial,
  passed,
  steps: 7,
  cost_usd: 0.018,
  trace_id: `t${row}${trial}`,
  at,
});

describe("streamedResults", () => {
  it("flattens batches and results, newest first", () => {
    const list = streamedResults({
      events: [
        { event: "handshake", line: HS },
        {
          event: "batch",
          results: [
            { row_id: "a", row_label: "Row 1", trial: 1, trace_id: "x" },
            { row_id: "b", row_label: "Row 2", trial: 1, trace_id: "y" },
          ],
        },
        result(17, 2, true),
      ],
    });
    expect(list.map((r) => r.rowLabel)).toEqual(["T17", "Row 2", "Row 1"]);
    expect(streamRowText(list[0])).toBe("T17 · trial 2 · 7 steps · $0.018");
    expect(streamRowText(list[1])).toBe("Row 2 · trial 1");
  });
});

describe("formatUsd", () => {
  it("3 significant decimals under a cent, else 2", () => {
    expect(formatUsd(0.004)).toBe("$0.004");
    expect(formatUsd(0.0184)).toBe("$0.0184");
    expect(formatUsd(0.21)).toBe("$0.21");
    expect(formatUsd(129)).toBe("$129.00");
    expect(formatUsd(null)).toBe("—");
  });
});

describe("uploadStatus", () => {
  it("today's upload states", () => {
    expect(uploadStatus({ status: "WaitingForResults" }).text).toBe(
      "Waiting for results",
    );
    expect(
      uploadStatus({
        status: "Running",
        trials: 3,
        progress: { done: 14, total: 30 },
      }).text,
    ).toBe("Receiving · 42 of 90");
    expect(uploadStatus({ status: "ExperimentEvaluation" }).text).toBe(
      "Scoring",
    );
    const done = uploadStatus({
      status: "Completed",
      progress: { done: 248, total: 248 },
    });
    expect(done.text).toBe("Completed · 248 results received");
    expect(done.pct).toBe(100);
  });
});

describe("sdkStatus", () => {
  const events = [
    { event: "handshake", line: HS },
    result(1, 1, true),
    result(1, 2, false),
    result(2, 1, true),
    result(2, 2, true),
  ];
  it("waiting / running / completed copy", () => {
    expect(sdkStatus({ state: "waiting" }).text).toBe("Waiting for results…");
    const running = sdkStatus(
      { state: "running", events, received: 4, expected: 90, failed: 1 },
      { now: Date.parse("2026-10-15T10:00:30Z") },
    );
    expect(running.text).toBe("Running · 4 of 90 attempts · 1 failed so far");
    const done = sdkStatus({
      state: "completed",
      events,
      received: 4,
      expected: 4,
      failed: 1,
    });
    expect(done.text).toBe(
      "Done · 3 of 4 attempts passed · 1 of 2 rows pass every trial",
    );
  });
  it("carry-over: new attempts and the run total", () => {
    const done = sdkStatus(
      { state: "completed", events, received: 18, expected: 18, failed: 2 },
      { carried: { attempts: 72, passed: 61, from: "#1C" } },
    );
    expect(done.text).toBe(
      "Done · 16 of 18 new attempts passed · run total 77 of 90 (72 carried from #1C)",
    );
  });
  it("stalled after 2 min without a heartbeat; stopped names who", () => {
    const s = sdkStatus(
      {
        state: "running",
        events,
        last_heartbeat_at: "2026-10-15T10:00:00Z",
      },
      { now: Date.parse("2026-10-15T10:03:00Z") },
    );
    expect(s.state).toBe("stalled");
    expect(
      sdkStatus(
        { state: "stopped", received: 52, expected: 90 },
        { stoppedBy: "Maya Chen" },
      ).text,
    ).toBe(
      "Stopped by Maya Chen at 52 of 90. Your script was told to finish its current rows.",
    );
  });
});

it("sdkStatus without pass info only counts attempts", () => {
  const events = [
    {
      event: "batch",
      results: [{ row_id: "a", row_label: "Row 1", trial: 1 }],
    },
  ];
  expect(
    sdkStatus({ state: "completed", events, received: 1, expected: 1 }).text,
  ).toBe("Done · 1 attempt received");
  expect(
    sdkStatus({ state: "running", events, received: 1, expected: 9 }).text,
  ).toBe("Running · 1 of 9 attempts");
});
