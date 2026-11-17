import { describe, expect, it } from "vitest";

import { raceModeOn, raceSummary } from "../raceMode";
import { storedRoutingOnly } from "../routingNotApplied";

// This gateway races no request, so race mode is a stored setting it does
// not run: the summary never says a request races, and Org config > Routing
// lists a stored race setting read-only.
describe("race mode on this gateway", () => {
  it("reads Fastest in any case or with underscores as strategy fastest", () => {
    expect(raceModeOn({ strategy: "Fastest" })).toBe(true);
    expect(raceModeOn({ strategy: " fastest " })).toBe(true);
    expect(raceModeOn({ fastest: { enabled: true } })).toBe(true);
    expect(raceModeOn({ strategy: "least_latency" })).toBe(false);
  });

  it("says a stored race setting is not run, never that a request races", () => {
    expect(raceSummary({ fastest: { enabled: true, max_concurrent: 2 } })).toBe(
      "Stored, not run on this gateway",
    );
    expect(raceSummary({})).toBe("Off");
  });

  it("lists a stored race setting read-only under routing.fastest", () => {
    const { ids, rows } = storedRoutingOnly({
      strategy: "fastest",
      fastest: { enabled: true, max_concurrent: 3, excluded_providers: ["x"] },
    });
    expect(ids).toEqual(["routing.fastest"]);
    expect(rows).toEqual([
      { label: "Strategy", value: "Fastest (race)" },
      { label: "Race mode", value: "On, up to 3 at once, excluding x" },
    ]);
  });

  it("lists nothing when no race setting is stored", () => {
    expect(storedRoutingOnly({ strategy: "weighted" })).toEqual({
      ids: [],
      rows: [],
    });
  });
});
