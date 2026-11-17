// A column menu's Sort by biggest regression and Sort by biggest improvement,
// by the read's published query contract. While the contract lists fixed
// choices, a launch build's read does not answer a sort by one run's
// difference: each entry stays in the menu, turned off with the shared line,
// and choosing it sorts nothing; no read asks for that sort, and a link that
// names it reads as the page's order. Once the contract takes the sort as a
// pattern, the entries sort as before, the orders by how a run's tries spread
// are sent and read as named, and one that names no metric still reads as
// the page's order. Every sort works in a build that is not a launch build.
import * as zod from "zod";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import { runResultsParams } from "src/api/develop/run-results";
import { buildGroupMenu } from "../groupMenu";
import { readResultsState } from "../useResultsUrlState";

const contract = vi.hoisted(() => ({ sort: null }));

vi.mock("src/generated/api-contracts/api.zod", async (importOriginal) => {
  const actual = await importOriginal();
  const real = actual.DatasetResultsReadQueryParams;
  return {
    ...actual,
    DatasetResultsReadQueryParams: {
      ...real,
      shape: {
        ...real.shape,
        get sort() {
          return contract.sort;
        },
      },
    },
  };
});

const FIXED = ["order", "-order", "input", "-input", "regressed"];
const fixedChoices = () => zod.enum(FIXED).default("order");
const pattern = () => zod.string().max(256).default("order");

const A = "run-a";
const B = "run-b";
const DEF = {
  key: "eval:correctness",
  name: "Correctness",
  output_type: "score",
  higher_is_better: true,
  runs: [A, B],
  versions: {},
};
const LABELS = ["Sort by biggest regression", "Sort by biggest improvement"];
const DELTA_DOWN = `delta:${DEF.key}:${B}:asc`;
const DELTA_UP = `delta:${DEF.key}:${B}:desc`;
// Orders by how a run's tries spread, and one that names no metric.
const SPREAD = [`variance:${DEF.key}:${B}`, `flakiness:${B}`];
const NAMES_NO_METRIC = [`variance:${B}`];

const sortItems = (sortBy) =>
  buildGroupMenu(
    { key: DEF.key, kind: "eval", def: DEF },
    {
      agg: "run",
      setAgg: () => {},
      firstCandidate: B,
      multiRun: true,
      showReasons: false,
      toggleReasons: () => {},
      hide: () => {},
      sortBy,
    },
  ).filter((i) => LABELS.includes(i.label));

beforeEach(() => {
  contract.sort = fixedChoices();
});
afterEach(() => setLaunchOverrideForTest(null));

describe("Sorting by one run's difference while the contract lists fixed choices", () => {
  it("keeps both menu entries, turned off with the shared line, and sorts nothing", () => {
    setLaunchOverrideForTest({ launch: true });
    const sortBy = vi.fn();
    const items = sortItems(sortBy);
    expect(items.map((i) => i.label)).toEqual(LABELS);
    for (const item of items) {
      expect(item.disabled).toBe(true);
      expect(item.secondary).toBe(NOT_IN_RELEASE);
      item.onClick();
    }
    expect(sortBy).not.toHaveBeenCalled();
  });

  it.each([DELTA_DOWN, DELTA_UP])("sends no sort=%s", (sort) => {
    setLaunchOverrideForTest({ launch: true });
    expect(runResultsParams({ runIds: [A, B], sort }).sort).toBeUndefined();
  });

  it.each([DELTA_DOWN, DELTA_UP])(
    "reads a sort=%s link as the page's order",
    (sort) => {
      setLaunchOverrideForTest({ launch: true });
      expect(readResultsState(new URLSearchParams({ sort })).sort).toBe(
        "order",
      );
    },
  );

  it.each(["-order", "input", "-input", "regressed"])(
    "sends and reads sort=%s as before",
    (sort) => {
      setLaunchOverrideForTest({ launch: true });
      expect(runResultsParams({ runIds: [A, B], sort }).sort).toBe(sort);
      expect(readResultsState(new URLSearchParams({ sort })).sort).toBe(sort);
    },
  );
});

describe("Sorting by one run's difference once the contract takes a pattern", () => {
  it("sorts by the entry's difference, and sends it", () => {
    setLaunchOverrideForTest({ launch: true });
    contract.sort = pattern();
    const sortBy = vi.fn();
    const items = sortItems(sortBy);
    for (const item of items) {
      expect(item.disabled).toBeFalsy();
      expect(item.secondary).toBeUndefined();
      item.onClick();
    }
    expect(sortBy.mock.calls.map(([sort]) => sort)).toEqual([
      DELTA_DOWN,
      DELTA_UP,
    ]);
    for (const sort of [DELTA_DOWN, DELTA_UP]) {
      expect(runResultsParams({ runIds: [A, B], sort }).sort).toBe(sort);
      expect(readResultsState(new URLSearchParams({ sort })).sort).toBe(sort);
    }
  });

  it.each(SPREAD)("sends sort=%s and reads its link as named", (sort) => {
    setLaunchOverrideForTest({ launch: true });
    contract.sort = pattern();
    expect(runResultsParams({ runIds: [A, B], sort }).sort).toBe(sort);
    expect(readResultsState(new URLSearchParams({ sort })).sort).toBe(sort);
  });

  it.each(NAMES_NO_METRIC)(
    "sends no sort=%s and reads its link as the page's order",
    (sort) => {
      setLaunchOverrideForTest({ launch: true });
      contract.sort = pattern();
      expect(runResultsParams({ runIds: [A, B], sort }).sort).toBeUndefined();
      expect(readResultsState(new URLSearchParams({ sort })).sort).toBe(
        "order",
      );
    },
  );
});

describe("Sorting by one run's difference in a build that is not a launch build", () => {
  it.each([fixedChoices, pattern])(
    "sorts by the entry's difference, as before (%#)",
    (shape) => {
      contract.sort = shape();
      const sortBy = vi.fn();
      const items = sortItems(sortBy);
      for (const item of items) {
        expect(item.disabled).toBeFalsy();
        expect(item.secondary).toBeUndefined();
        item.onClick();
      }
      expect(sortBy.mock.calls.map(([sort]) => sort)).toEqual([
        DELTA_DOWN,
        DELTA_UP,
      ]);
      expect(runResultsParams({ runIds: [A, B], sort: DELTA_DOWN }).sort).toBe(
        DELTA_DOWN,
      );
    },
  );
});
