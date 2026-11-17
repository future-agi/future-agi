// The results page's "Most variable across trials" and "Most flaky" orders.
// The read answers `variance:<metric>:<run>` (rows whose tries spread most
// on the metric first) and `flakiness:<run>` (rows whose tries split most
// evenly between passing and failing first). A launch build offers both once
// the read's published query contract takes the sort as a pattern, and sends
// them as the page names them; a `variance:<run>` that names no metric is not
// offered. The Sort menu's Most variable entry asks for a metric and a run.
import { afterEach, describe, expect, it, vi } from "vitest";
import * as zod from "zod";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";

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

import { RESULTS_SORT_KINDS, resultsSortOffered } from "../results-shows";
import { runResultsParams } from "../run-results";
import {
  SORT_ENTRIES,
  parseSort,
  sortFitsView,
  sortLabel,
  sortString,
  splitSort,
} from "src/sections/experiment-detail/results/sortModel";

const RUN = "7b2a1d63-3333-4e8f-9cab-2a4bab3f4e5c";
const METRIC = "eval:correctness";
const VARIABLE = `variance:${METRIC}:${RUN}`;
const FLAKY = `flakiness:${RUN}`;
const NAMES_NO_METRIC = `variance:${RUN}`;

const FIXED = ["order", "-order", "input", "-input", "regressed"];
const fixedChoices = () => zod.enum(FIXED).default("order");
const pattern = () => zod.string().max(256).default("order");

afterEach(() => setLaunchOverrideForTest(null));

describe("Most variable and most flaky once the contract takes a pattern", () => {
  it("are kinds of order the read answers", () => {
    expect(RESULTS_SORT_KINDS).toEqual(
      expect.arrayContaining(["variance", "flakiness"]),
    );
  });

  it.each([
    VARIABLE,
    `variance:8c3b2e74-4444-4f9a-8dbc-3b5cbc4a5f6d:${RUN}`,
    FLAKY,
  ])("offers and sends sort=%s", (sort) => {
    setLaunchOverrideForTest({ launch: true });
    contract.sort = pattern();
    expect(resultsSortOffered(sort)).toBe(true);
    expect(runResultsParams({ runIds: [RUN], sort }).sort).toBe(sort);
  });

  it("does not offer or send a variance order that names no metric", () => {
    setLaunchOverrideForTest({ launch: true });
    contract.sort = pattern();
    expect(resultsSortOffered(NAMES_NO_METRIC)).toBe(false);
    expect(
      runResultsParams({ runIds: [RUN], sort: NAMES_NO_METRIC }).sort,
    ).toBeUndefined();
  });
});

describe("Most variable and most flaky while the contract lists fixed choices", () => {
  it.each([VARIABLE, FLAKY])("does not offer sort=%s", (sort) => {
    setLaunchOverrideForTest({ launch: true });
    contract.sort = fixedChoices();
    expect(resultsSortOffered(sort)).toBe(false);
  });
});

describe("The Sort menu's entries", () => {
  it("ask for a metric and a run for Most variable, and a run for Most flaky", () => {
    const entry = (id) => SORT_ENTRIES.find((e) => e.id === id);
    expect(entry("variance").fields).toEqual(["metric", "run"]);
    expect(entry("flakiness").fields).toEqual(["run"]);
    expect(sortString("variance", { metric: METRIC, run: RUN })).toBe(VARIABLE);
    expect(sortString("flakiness", { metric: METRIC, run: RUN })).toBe(FLAKY);
  });

  it("read a Most variable order back with its metric and run", () => {
    expect(splitSort(VARIABLE)).toEqual({
      kind: "variance",
      metric: METRIC,
      run: RUN,
    });
    expect(parseSort(VARIABLE)).toEqual({
      entryId: "variance",
      metric: METRIC,
      run: RUN,
    });
    expect(parseSort(FLAKY)).toEqual({ entryId: "flakiness", run: RUN });
    expect(
      sortLabel(VARIABLE, {
        metricsByKey: { [METRIC]: { name: "Correctness" } },
        runsById: { [RUN]: { label: "#2B" } },
      }),
    ).toBe("Sort: Most variable across trials · Correctness · #2B");
  });

  it("fit a view that holds their run", () => {
    for (const sort of [VARIABLE, FLAKY]) {
      expect([sort, sortFitsView(sort, { runIds: [RUN] })]).toEqual([
        sort,
        true,
      ]);
      expect([sort, sortFitsView(sort, { runIds: ["another"] })]).toEqual([
        sort,
        false,
      ]);
    }
  });
});
