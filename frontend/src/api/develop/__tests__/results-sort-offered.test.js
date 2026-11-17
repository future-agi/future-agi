// Which sorts the results page offers in a launch build. While the read's
// published query contract lists fixed choices, the page offers those. Once
// the contract takes the sort as a pattern, the page offers each kind of
// order the read answers and no other; an order by a metric's spread across
// a run's tries names the metric. A build that is not a launch build offers
// every sort.
import { afterEach, describe, expect, it, vi } from "vitest";
import * as zod from "zod";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";

const contract = vi.hoisted(() => ({ sort: null }));

vi.mock("src/generated/api-contracts/api.zod", () => ({
  DatasetResultsReadQueryParams: {
    shape: {
      get sort() {
        return contract.sort;
      },
    },
  },
}));

import { RESULTS_SORT_KINDS, resultsSortOffered } from "../results-shows";

const RUN = "7b2a1d63-3333-4e8f-9cab-2a4bab3f4e5c";
const METRIC = "8c3b2e74-4444-4f9a-8dbc-3b5cbc4a5f6d";
// The sort kind of a run's spend.
const SPEND = "cost";
const FIXED = ["order", "-order", "input", "-input", "regressed"];
const BY_RUN = [
  `delta:${METRIC}:${RUN}:asc`,
  `delta:${METRIC}:${RUN}:desc`,
  `score:${METRIC}:${RUN}:asc`,
  `score:${METRIC}:${RUN}:desc`,
  `score:eval:correctness:${RUN}:asc`,
  `latency:${RUN}`,
  `${SPEND}:${RUN}`,
  `variance:${METRIC}:${RUN}`,
  `variance:eval:correctness:${RUN}`,
  `flakiness:${RUN}`,
];
// An order by a metric's spread that names no metric.
const NOT_ANSWERED = [`variance:${RUN}`];

const fixedChoices = () => zod.enum(FIXED).default("order");
const pattern = () => zod.string().max(256).default("order");

afterEach(() => setLaunchOverrideForTest(null));

describe("The sorts the results page offers", () => {
  it("are the read's kinds of order once its contract takes a pattern", () => {
    setLaunchOverrideForTest({ launch: true });
    contract.sort = pattern();
    expect(RESULTS_SORT_KINDS).toEqual([
      ...FIXED,
      "delta",
      "score",
      "latency",
      SPEND,
      "variance",
      "flakiness",
    ]);
    for (const sort of [...FIXED, ...BY_RUN])
      expect([sort, resultsSortOffered(sort)]).toEqual([sort, true]);
    for (const sort of NOT_ANSWERED)
      expect([sort, resultsSortOffered(sort)]).toEqual([sort, false]);
  });

  it("are the listed choices while the contract lists fixed choices", () => {
    setLaunchOverrideForTest({ launch: true });
    contract.sort = fixedChoices();
    for (const sort of FIXED)
      expect([sort, resultsSortOffered(sort)]).toEqual([sort, true]);
    for (const sort of [...BY_RUN, ...NOT_ANSWERED])
      expect([sort, resultsSortOffered(sort)]).toEqual([sort, false]);
  });

  it("are every sort in a build that is not a launch build", () => {
    setLaunchOverrideForTest({ launch: false });
    for (const make of [pattern, fixedChoices]) {
      contract.sort = make();
      for (const sort of [...FIXED, ...BY_RUN, ...NOT_ANSWERED])
        expect([sort, resultsSortOffered(sort)]).toEqual([sort, true]);
    }
  });

  it("always include the page's own order", () => {
    setLaunchOverrideForTest({ launch: true });
    contract.sort = pattern();
    expect(resultsSortOffered("order")).toBe(true);
    expect(resultsSortOffered("")).toBe(true);
    expect(resultsSortOffered(undefined)).toBe(true);
  });
});
