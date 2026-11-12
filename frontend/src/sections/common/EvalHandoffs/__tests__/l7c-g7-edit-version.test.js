// L7c g7 (transfer:hover-card, transfer:score-card): an edit made in an
// experiment ("v2.1") is not a saved template version: the transfer says
// so and opens the version it starts from.
import { describe, expect, it } from "vitest";
import { editVersionOf, evalLinkOf } from "../evalLinks";

describe("evalLinkOf for an experiment-level edit", () => {
  it("reads v2.1 as the first edit of v2", () => {
    expect(editVersionOf("v2.1")).toEqual({ id: "2.1", base: 2 });
    expect(editVersionOf("v3")).toBeNull();
    expect(editVersionOf(null)).toBeNull();
  });

  it("opens v2 and says the run used an edit of it", () => {
    const link = evalLinkOf({
      templateId: "t1",
      version: "v2.1",
      name: "Correctness",
      versions: [{ version_number: 1 }, { version_number: 2 }],
    });
    expect(link.href).toBe("/dashboard/evaluations/t1?v=2");
    expect(link.missing).toBe(false);
    expect(link.edited).toBe(true);
    expect(link.tooltip).toMatch(/edit of v2 made in this experiment/);
    expect(link.tooltip).not.toMatch(/no longer lists/);
  });

  it("a template version keeps its own link", () => {
    const link = evalLinkOf({
      templateId: "t1",
      version: "v2",
      name: "Correctness",
      versions: [{ version_number: 2 }],
    });
    expect(link.href).toBe("/dashboard/evaluations/t1?v=2");
    expect(link.edited).toBeUndefined();
  });
});
