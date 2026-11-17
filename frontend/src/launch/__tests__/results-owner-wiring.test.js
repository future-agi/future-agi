import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { describe, expect, it } from "vitest";
import { isContractedApiPath } from "src/api/contracts/api-surface";
import { absentTemplateOf } from "../absentPathGuard";

const read = (path) =>
  readFileSync(resolve(process.cwd(), "src", path), "utf8");
const results = "sections/experiment-detail/";

describe("Results owner wiring", () => {
  it.each(["IterateButton", "IterateDock", "IterateFromHereButton"])(
    "gates %s before registering any hooks, commands or URL actions",
    (name) => {
      const source = read(`${results}iterate/${name}.jsx`);
      expect(source).toContain(
        [
          `const Launch${name} = (props) =>`,
          '  isSurfaceShown("exp.results.iterateDock") ? (',
          `    <${name} {...props} />`,
          "  ) : null;",
          "",
          `export default Launch${name};`,
        ].join("\n"),
      );
      if (name === "IterateDock") {
        expect(source).toContain("  useIterateParams();");
        expect(source).toContain('document.addEventListener("keydown", onKey)');
      } else if (name === "IterateButton") {
        expect(source).toContain('id: "testAFix.open"');
      }
    },
  );

  it("gates run menu, selection and finding doors independently of Summary", () => {
    const bar = read(`${results}results/RunBar.jsx`);
    expect(bar).toContain(
      '...(isSurfaceShown("exp.results.iterateDock") && canIterate(run)',
    );
    expect(bar).toContain('...(isSurfaceShown("exp.results.matrix")');
    expect(read(`${results}results/SelectionBar.jsx`)).toContain(
      '...(!isSurfaceShown("exp.results.iterateDock") ||',
    );
    expect(read(`${results}results/WhyFailsStrip.jsx`)).toContain(
      '!isSurfaceShown("exp.results.iterateDock") ||',
    );
    // Both finding presentations consume the guarded fixHref, not a new URL.
    expect(read(`${results}results/SeeWhyButton.jsx`)).toContain(
      "why?.fixHref && why.rows?.length",
    );
  });

  it("keeps unsupported Summary readers out of both hosts", () => {
    const summary = read(
      `${results}ExperimentSummary/Analysis/ExperimentAnalysisView.jsx`,
    );
    expect(summary).toContain("const query = useRunAnalysis(datasetId, {");
    expect(summary).not.toMatch(
      /import[^;]*(?:DerivedMetrics|Classification|Workbench)|useDerivedMetrics|useClassification/,
    );
    for (const template of [
      "/model-hub/datasets/{dataset_id}/derived-metrics/",
      "/model-hub/datasets/{dataset_id}/classification/",
    ]) {
      expect(isContractedApiPath(template), template).toBe(false);
      expect(absentTemplateOf(template.replace("{dataset_id}", "d1"))).toBe(
        template,
      );
    }
  });
});
