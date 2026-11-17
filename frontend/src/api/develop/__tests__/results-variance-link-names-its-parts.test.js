// A "Most variable across trials" order names a metric and a run:
// `variance:<metric>:<run>`, where the metric key may itself hold colons.
// The read refuses one whose metric or run is empty, so a link such as
// `variance::<run>` is neither offered nor sent: a launch build reads it as
// the page's order, and every build reads it as an order that does not fit
// the view, so the view's default applies. A metric key with colons still
// works.
import { afterEach, describe, expect, it, vi } from "vitest";
import * as zod from "zod";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";

const contract = vi.hoisted(() => ({ sort: null }));

vi.mock("src/generated/api-contracts/api.zod", async (importOriginal) => {
  const actual = await importOriginal();
  const real = actual.DatasetResultsReadQueryParams;
  const realSort = real.shape.sort;
  return {
    ...actual,
    DatasetResultsReadQueryParams: {
      ...real,
      shape: {
        ...real.shape,
        get sort() {
          return contract.sort ?? realSort;
        },
      },
    },
  };
});

import { resultsSortOffered } from "../results-shows";
import { runResultsParams } from "../run-results";
import {
  sortFitsView,
  sortForView,
  splitSort,
} from "src/sections/experiment-detail/results/sortModel";

const BASE = "0b5b7b9e-2222-4c8f-9a8e-0e2f8d1d2c3a";
const RUN = "7b2a1d63-3333-4e8f-9cab-2a4bab3f4e5c";
const METRIC = "eval:correctness";
const VIEW = { runIds: [BASE, RUN], baselineId: BASE };
// The view's default order with a baseline and another run.
const DEFAULT = "regressed";
const pattern = () => zod.string().max(256).default("order");

const NO_METRIC = `variance::${RUN}`;
const NO_RUN = `variance:${METRIC}:`;
const NOTHING = "variance::";
const INCOMPLETE = [NO_METRIC, NO_RUN, NOTHING];
const WITH_COLONS = `variance:${METRIC}:${RUN}`;

afterEach(() => {
  contract.sort = null;
  setLaunchOverrideForTest(null);
});

describe("A variance link with an empty metric or run in a launch build", () => {
  it.each(INCOMPLETE)("is not offered or sent: %s", (sort) => {
    setLaunchOverrideForTest({ launch: true });
    contract.sort = pattern();
    expect(resultsSortOffered(sort)).toBe(false);
    expect(runResultsParams({ ...VIEW, sort }).sort).toBeUndefined();
  });

  it.each(INCOMPLETE)("reads as the page's order: %s", (sort) => {
    setLaunchOverrideForTest({ launch: true });
    contract.sort = pattern();
    expect(sortForView(sort, VIEW)).toBe("order");
  });

  it("offers and sends a metric key that holds colons", () => {
    setLaunchOverrideForTest({ launch: true });
    contract.sort = pattern();
    expect(resultsSortOffered(WITH_COLONS)).toBe(true);
    expect(sortForView(WITH_COLONS, VIEW)).toBe(WITH_COLONS);
    expect(runResultsParams({ ...VIEW, sort: WITH_COLONS }).sort).toBe(
      WITH_COLONS,
    );
  });
});

describe("A variance link with an empty metric or run in any build", () => {
  it.each(INCOMPLETE)("does not fit the view: %s", (sort) => {
    setLaunchOverrideForTest({ launch: false });
    expect(sortFitsView(sort, VIEW)).toBe(false);
  });

  it.each(INCOMPLETE)(
    "reads as the view's default and is not sent: %s",
    (sort) => {
      setLaunchOverrideForTest({ launch: false });
      const read = sortForView(sort, VIEW);
      expect(read).toBe(DEFAULT);
      expect(runResultsParams({ ...VIEW, sort: read }).sort).toBe(DEFAULT);
    },
  );

  it("keeps a metric key that holds colons, and its run", () => {
    setLaunchOverrideForTest({ launch: false });
    expect(splitSort(WITH_COLONS)).toEqual({
      kind: "variance",
      metric: METRIC,
      run: RUN,
    });
    expect(sortFitsView(WITH_COLONS, VIEW)).toBe(true);
    expect(sortForView(WITH_COLONS, VIEW)).toBe(WITH_COLONS);
    // A key that ends in a colon is still a key.
    const trailing = `variance:${METRIC}::${RUN}`;
    expect(splitSort(trailing).metric).toBe(`${METRIC}:`);
    expect(sortFitsView(trailing, VIEW)).toBe(true);
  });

  it("does not fit a variance order of a run out of view", () => {
    setLaunchOverrideForTest({ launch: false });
    expect(sortFitsView(`variance:${METRIC}:gone`, VIEW)).toBe(false);
  });
});
