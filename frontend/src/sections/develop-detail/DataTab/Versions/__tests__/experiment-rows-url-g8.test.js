// v2.1 g8 (door:run-on-rows:*): the selection and Row view doors write
// `rows=` with `new_experiment=1`, so a reload keeps the picked rows.
import { describe, expect, it } from "vitest";
import { withExperimentRows } from "../experimentRowsUrl";
import { parseRunOnRows } from "src/sections/common/RunSources/runOnRows";

describe("withExperimentRows", () => {
  it("keeps the page's keys and adds the drawer + rows", () => {
    const s = withExperimentRows("?tab=data&row=r12", ["r3", "r5", "r7"]);
    const q = parseRunOnRows(s);
    expect(q.open).toBe(true);
    expect(q.rows).toEqual(["r3", "r5", "r7"]);
    expect(new URLSearchParams(s).get("tab")).toBe("data");
  });
  it("no rows drops a stale rows key", () => {
    const s = withExperimentRows("?rows=a,b", []);
    expect(new URLSearchParams(s).get("rows")).toBeNull();
  });
});
