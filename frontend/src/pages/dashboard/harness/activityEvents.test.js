import { describe, expect, it } from "vitest";
import { compactActivityEvents } from "./activityEvents";

describe("compactActivityEvents", () => {
  it("keeps only the newest consecutive duplicate progress heartbeat", () => {
    const events = [
      {
        event_id: "1",
        type: "harness.stage.progress",
        payload: { stage: "understanding", detail: "31 files" },
      },
      {
        event_id: "2",
        type: "harness.stage.progress",
        payload: { stage: "understanding", detail: "31 files" },
      },
      {
        event_id: "3",
        type: "harness.stage.progress",
        payload: { stage: "understanding", detail: "31 files" },
      },
    ];
    expect(compactActivityEvents(events)).toEqual([events[2]]);
  });

  it("preserves distinct progress and non-progress events", () => {
    const events = [
      {
        event_id: "1",
        type: "harness.stage.progress",
        payload: { detail: "one" },
      },
      {
        event_id: "2",
        type: "harness.stage.progress",
        payload: { detail: "two" },
      },
      {
        event_id: "3",
        type: "harness.stage.changed",
        payload: { stage: "building" },
      },
    ];
    expect(compactActivityEvents(events)).toEqual(events);
  });
});
