import { describe, expect, it } from "vitest";
import { compactActivityEvents } from "./activityEvents";

const progress = (id, detail, wallTime) => ({
  event_id: id,
  type: "harness.stage.progress",
  wall_time: wallTime,
  payload: {
    stage: "understand",
    activity: "source_inspection",
    detail,
  },
});

describe("compactActivityEvents", () => {
  it("keeps only the newest identical consecutive heartbeat", () => {
    const result = compactActivityEvents([
      progress("first", "Understanding source · inspecting 31 relevant files", "10:00"),
      progress("second", "Understanding source · inspecting 31 relevant files", "10:15"),
      progress("third", "Understanding source · inspecting 31 relevant files", "10:30"),
    ]);

    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({ event_id: "third", wall_time: "10:30" });
  });

  it("preserves changed progress and real stage transitions", () => {
    const started = {
      event_id: "started",
      type: "harness.stage.started",
      payload: { stage: "understand" },
    };
    const result = compactActivityEvents([
      started,
      progress("scan", "Understanding source · inspecting 31 relevant files", "10:15"),
      progress("contract", "Understanding source · validating contract", "10:30"),
    ]);

    expect(result.map((event) => event.event_id)).toEqual([
      "started",
      "scan",
      "contract",
    ]);
  });

  it("deduplicates repeated event ids", () => {
    const event = progress("same", "Building image", "10:15");
    expect(compactActivityEvents([event, event])).toEqual([event]);
  });
});
