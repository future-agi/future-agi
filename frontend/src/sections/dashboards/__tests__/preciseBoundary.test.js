import { describe, expect, it } from "vitest";
import { isPinnedWindowExcluded } from "../dashboardDateRange";
import cases from "./fixtures/preciseBoundaryCases.json";

// These fixtures are generated and checked by actual DashboardTimeRangeSerializer
// + dashboard_result, with an independent Python datetime comparison and read count.
describe("microsecond dashboard observation boundaries", () => {
  it.each(cases)("$input_point / $boundary / $delta_us us", (testCase) => {
    const {
      config,
      saved_run: run,
      range,
      serialized_range: serialized,
      included,
    } = testCase;
    expect(isPinnedWindowExcluded(config, range, run)).toBe(!included);
    expect(isPinnedWindowExcluded(config, serialized, run)).toBe(!included);
    expect(
      isPinnedWindowExcluded(config, range, {
        ...run,
        window_end: testCase.input_point,
      }),
    ).toBe(!included);
  });
  const sample = cases[0];
  const start = "2026-10-25T00:00:00.000Z";
  const end = "2026-10-26T00:00:00.000Z";
  it.each(["date", "milliseconds", "iso", "date-only"])(
    "preserves %s input behavior",
    (kind) => {
      const cast = (value) =>
        kind === "date"
          ? new Date(value)
          : kind === "milliseconds"
            ? new Date(value).getTime()
            : kind === "date-only"
              ? value.slice(0, 10)
              : value;
      const run = {
        ...sample.saved_run,
        window_start: cast(start),
        window_end: cast(end),
      };
      expect(
        isPinnedWindowExcluded(
          sample.config,
          { start: cast(end), end: cast(end) },
          run,
        ),
      ).toBe(false);
      expect(
        isPinnedWindowExcluded(
          sample.config,
          { start: cast(start), end: cast(start) },
          run,
        ),
      ).toBe(true);
    },
  );
  it.each([
    null,
    "",
    "unknown",
    "2026-02-29T00:00:00.000001Z",
    "2026-10-26T24:00:00Z",
    "2026-10-26T00:00:00.000001+24:00",
    new Date(NaN),
    Infinity,
    {},
  ])("unknown/invalid provenance %s does not imply exclusion", (value) => {
    expect(
      isPinnedWindowExcluded(
        sample.config,
        { start, end },
        { ...sample.saved_run, window_end: value },
      ),
    ).toBe(false);
    expect(
      isPinnedWindowExcluded(
        sample.config,
        { start, end: value },
        sample.saved_run,
      ),
    ).toBe(false);
  });
  it("retains run identity, single-metric and range ordering guards", () => {
    expect(
      isPinnedWindowExcluded(
        sample.config,
        { start, end },
        { ...sample.saved_run, id: "other-run" },
      ),
    ).toBe(false);
    expect(
      isPinnedWindowExcluded(
        { metrics: [...sample.config.metrics, ...sample.config.metrics] },
        { start, end },
        sample.saved_run,
      ),
    ).toBe(false);
    expect(
      isPinnedWindowExcluded(
        sample.config,
        { start: end, end: start },
        sample.saved_run,
      ),
    ).toBe(false);
    expect(
      isPinnedWindowExcluded(
        sample.config,
        { start, end },
        { ...sample.saved_run, window_start: sample.saved_run.window_end },
      ),
    ).toBe(false);
  });
});
