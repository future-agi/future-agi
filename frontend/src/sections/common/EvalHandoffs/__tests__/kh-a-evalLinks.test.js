import { describe, expect, it } from "vitest";
import {
  evalLinkOf,
  evalPageHref,
  resolveTemplateId,
  versionNumberOf,
} from "../evalLinks";

describe("evalLinks", () => {
  it("parses versions", () => {
    expect(versionNumberOf("v2")).toBe(2);
    expect(versionNumberOf("V12")).toBe(12);
    expect(versionNumberOf(2)).toBe(2);
    expect(versionNumberOf("3")).toBe(3);
    expect(versionNumberOf(null)).toBeNull();
    expect(versionNumberOf("")).toBeNull();
    expect(versionNumberOf("v2.1")).toBeNull();
    expect(versionNumberOf(0)).toBeNull();
  });

  it("builds the Evals href with ?v= (EvalDetailPage reads `v`)", () => {
    expect(evalPageHref({ templateId: "t1", version: "v2" })).toBe(
      "/dashboard/evaluations/t1?v=2",
    );
    expect(evalPageHref({ templateId: "t1", version: 3 })).toBe(
      "/dashboard/evaluations/t1?v=3",
    );
    expect(evalPageHref({ templateId: "t1" })).toBe(
      "/dashboard/evaluations/t1",
    );
    expect(evalPageHref({ version: "v2" })).toBeNull();
  });

  it("resolves the template id in order and never guesses", () => {
    const list = [
      { id: "uem-1", template_id: "t-a", metric_key: "eval:a" },
      { id: "uem-2", template_id: "t-b" },
    ];
    expect(
      resolveTemplateId({
        def: { template_id: "t-own", key: "eval:a" },
        datasetEvals: list,
      }),
    ).toBe("t-own");
    expect(
      resolveTemplateId({ def: { key: "eval:a" }, datasetEvals: list }),
    ).toBe("t-a");
    expect(
      resolveTemplateId({
        def: { key: "eval:z", user_eval_id: "uem-2" },
        datasetEvals: list,
      }),
    ).toBe("t-b");
    expect(
      resolveTemplateId({
        def: { key: "eval:z", name: "B" },
        datasetEvals: list,
      }),
    ).toBeNull();
    expect(
      resolveTemplateId({ def: null, datasetEvals: undefined }),
    ).toBeNull();
  });

  it("checks the version against the template's list", () => {
    const versions = [
      { version_number: 3, is_default: true },
      { version_number: 2 },
    ];
    expect(
      evalLinkOf({
        templateId: "t",
        version: "v2",
        name: "Correctness",
        versions,
      }),
    ).toEqual({
      href: "/dashboard/evaluations/t?v=2",
      tooltip: "Open Correctness v2 in Evals",
      missing: false,
    });
    expect(
      evalLinkOf({
        templateId: "t",
        version: "v1",
        name: "Correctness",
        versions,
      }),
    ).toEqual({
      href: "/dashboard/evaluations/t",
      tooltip:
        "This run used v1, which Correctness no longer lists. Opens the current version (v3).",
      missing: true,
    });
    // Not loaded yet: keep the run's version.
    expect(
      evalLinkOf({
        templateId: "t",
        version: "v2",
        name: "Tone",
        versions: null,
      }).href,
    ).toBe("/dashboard/evaluations/t?v=2");
    expect(
      evalLinkOf({ templateId: "t", version: null, name: "Tone" }).tooltip,
    ).toBe("Open Tone in Evals");
    expect(evalLinkOf({ templateId: null, version: "v2" })).toBeNull();
  });
});
