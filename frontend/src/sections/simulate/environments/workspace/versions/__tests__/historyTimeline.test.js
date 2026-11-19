import { describe, it, expect } from "vitest";
import { toVersions } from "src/api/simulate-environments/versions";
import { filterEntries, groupByDay, timelineEntries } from "../historyTimeline";

const v = (id, label, createdAt, extra = {}) => ({
  id,
  label,
  created_at: createdAt,
  status: "active",
  ...extra,
});

// Two agent versions share a label; s3 and a1 are the defaults.
const SAMPLE = {
  environment: [v("e1", "v1", "2026-10-28T20:10:00", { is_default: true })],
  suite: [
    v("s3", "v3", "2026-11-04T20:45:00", { is_default: true }),
    v("s2", "v2", "2026-10-28T20:11:00"),
    v("s1", "v1", "2026-10-28T20:10:30"),
  ],
  agent: [
    v("a3", "fix-live-test", "2026-11-02T12:27:00"),
    v("a2", "fix-live-test", "2026-11-02T12:26:00"),
    v("a1", "v1", "2026-09-25T15:44:00", { is_default: true }),
  ],
};

describe("historyTimeline", () => {
  it("merges the three lists newest first and keys rows by piece and id", () => {
    const entries = timelineEntries(toVersions(SAMPLE));
    expect(entries.map((e) => e.key)).toEqual([
      "suite:s3",
      "agent:a3",
      "agent:a2",
      "suite:s2",
      "suite:s1",
      "environment:e1",
      "agent:a1",
    ]);
  });

  it("badges the default as in use (being tested for the agent) and the newest active one as latest", () => {
    const byKey = Object.fromEntries(
      timelineEntries(toVersions(SAMPLE)).map((e) => [e.key, e.badges]),
    );
    expect(byKey["suite:s3"]).toEqual(["inUse", "latest"]);
    expect(byKey["agent:a1"]).toEqual(["tested"]);
    expect(byKey["agent:a3"]).toEqual(["latest"]);
    expect(byKey["suite:s2"]).toEqual([]);
  });

  it("badges draft and discarded versions and never calls them latest", () => {
    const entries = timelineEntries(
      toVersions({
        agent: [
          v("d", "v3", "2026-11-03T10:00:00", { status: "draft" }),
          v("x", "v2", "2026-11-02T10:00:00", { status: "discarded" }),
          v("a", "v1", "2026-11-01T10:00:00"),
        ],
      }),
    );
    expect(entries.map((e) => e.badges)).toEqual([
      ["draft"],
      ["discarded"],
      ["latest"],
    ]);
  });

  it("puts undated versions in an Earlier group after the dated days", () => {
    const entries = timelineEntries(
      toVersions({
        environment: [
          v("e3", "v3", "2026-11-19T09:00:00"),
          v("e2", "v2", "2026-11-18T23:30:00"),
          v("e1", "v1", null),
        ],
        suite: [v("s1", "v1", "2026-11-04T08:00:00")],
      }),
    );
    const groups = groupByDay(entries, new Date("2026-11-19T10:00:00"));
    expect(groups.map((g) => g.label)).toEqual([
      "Today",
      "Yesterday",
      "Nov 4",
      "Earlier",
    ]);
    expect(groups.at(-1).entries.map((e) => e.key)).toEqual(["environment:e1"]);
  });

  it("filters by kind", () => {
    const entries = timelineEntries(toVersions(SAMPLE));
    expect(filterEntries(entries, "suite").map((e) => e.key)).toEqual([
      "suite:s3",
      "suite:s2",
      "suite:s1",
    ]);
    expect(filterEntries(entries, "all")).toHaveLength(7);
  });

  it("names where a pushed version came from with its short commit", () => {
    const [entry] = timelineEntries(
      toVersions({
        agent: [
          v("p", "v4", "2026-11-05T10:00:00", {
            origin: "push",
            source: { commit_sha: "5fa416d9c0ffee" },
          }),
        ],
      }),
    );
    expect(entry.originLine).toBe("Pushed · 5fa416d");
  });
});
