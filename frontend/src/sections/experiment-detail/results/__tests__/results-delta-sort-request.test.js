// A results column's menu sorts the grid by one run's difference from the
// baseline on that column's metric. "Sort by biggest regression" asks the
// read for `delta:<metric>:<run>:asc` and "Sort by biggest improvement" for
// `delta:<metric>:<run>:desc`, and the grid's request carries the sort as
// the menu set it.
import { describe, expect, it, vi } from "vitest";

import { runResultsParams } from "src/api/develop/run-results";
import { buildGroupMenu } from "../groupMenu";

const BASELINE = "6a1f0c52-2222-4d7e-8b9f-1f3a9e2e3d4b";
const RUN = "7b2a1d63-3333-4e8f-9cab-2a4bab3f4e5c";
const METRIC = "8c3b2e74-4444-4f9a-8dbc-3b5cbc4a5f6d";
const DEF = {
  key: METRIC,
  name: "Correctness",
  output_type: "score",
  higher_is_better: true,
  runs: [BASELINE, RUN],
  versions: {},
};

// The sort a menu item sets when it is chosen.
const chosen = (label) => {
  const sortBy = vi.fn();
  const items = buildGroupMenu(
    { key: DEF.key, kind: "eval", def: DEF },
    {
      agg: "run",
      setAgg: () => {},
      firstCandidate: RUN,
      multiRun: true,
      showReasons: false,
      toggleReasons: () => {},
      hide: () => {},
      sortBy,
    },
  );
  const item = items.find((i) => i.label === label);
  expect(item.disabled).toBe(false);
  item.onClick();
  expect(sortBy).toHaveBeenCalledTimes(1);
  return sortBy.mock.calls[0][0];
};

describe("A results column's sort by a run's difference", () => {
  it("reaches the read as the menu set it", () => {
    for (const [label, direction] of [
      ["Sort by biggest regression", "asc"],
      ["Sort by biggest improvement", "desc"],
    ]) {
      const sort = chosen(label);
      expect(sort).toBe(`delta:${METRIC}:${RUN}:${direction}`);
      const params = runResultsParams({
        runIds: [BASELINE, RUN],
        baselineId: BASELINE,
        sort,
      });
      expect(params.sort).toBe(sort);
      expect(params.runs).toBe(`${BASELINE},${RUN}`);
      expect(params.baseline).toBe(BASELINE);
    }
  });
});
