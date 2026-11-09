import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { bpText } from "./pinCopyBp.fixture";

const source = (path) =>
  readFileSync(
    new URL(
      "../../../../" + path.replace("frontend/src/", ""),
      import.meta.url,
    ),
    "utf8",
  );

// Shared tooltip rows bind to their definition; mounted tests verify each use.
const paths = {
  "A1-01": "frontend/src/sections/evals/Helpers/evalUsageColumns.jsx",
  "A1-02": "frontend/src/sections/evals/Helpers/evalUsageColumns.jsx",
  "A1-03": "frontend/src/sections/evals/components/EvalUsageTab.jsx",
  "A1-04": "frontend/src/sections/evals/components/EvalUsageTab.jsx",
  "A1-05": "frontend/src/sections/common/EvalPicker/EvalPickerConfigFull.jsx",
  "A1-06": "frontend/src/sections/common/EvalPicker/EvalPickerConfigFull.jsx",
  "A1-07": "frontend/src/sections/common/EvalPicker/EvalPickerConfigFull.jsx",
  "A1-08": "frontend/src/sections/common/EvalPicker/EvalPickerConfigFull.jsx",
  "A1-09": "frontend/src/sections/common/EvalPicker/EvalPickerConfigFull.jsx",
  "A1-10": "frontend/src/sections/common/EvalPicker/EvalPickerConfigFull.jsx",
  "A1-11": "frontend/src/sections/evals/components/TestPlayground.jsx",
  "A2-01": "frontend/src/sections/evals/Helpers/evalVersionCopy.js",
  "A2-02": "frontend/src/sections/evals/Helpers/evalVersionCopy.js",
  "A2-03": "frontend/src/sections/evals/Helpers/evalVersionCopy.js",
  "A2-04": "frontend/src/sections/evals/Helpers/evalVersionCopy.js",
  "A2-05": "frontend/src/sections/evals/Helpers/evalVersionCopy.js",
  "A2-06": "frontend/src/sections/evals/Helpers/evalVersionCopy.js",
  "A3-01": "frontend/src/sections/evals/components/CompositeDetailPanel.jsx",
  "A3-02": "frontend/src/sections/evals/components/CompositeDetailPanel.jsx",
  "A4-01":
    "frontend/src/sections/evals/EvalDetails/EvalsFeedback/AddEvalsFeedbackDrawer.jsx",
  "A4-02":
    "frontend/src/sections/evals/EvalDetails/EvalsFeedback/AddEvalsFeedbackDrawer.jsx",
};

const removedSelectUnits = [
  "A1-05",
  "A1-06",
  "A1-07",
  "A1-08",
  "A1-09",
  "A2-04",
];

describe("BP activated copy boundaries", () => {
  it.each(
    Object.keys(paths).filter((unitId) => !removedSelectUnits.includes(unitId)),
  )("%s contains its byte-exact approved text", (unitId) => {
    expect(source(paths[unitId])).toContain(bpText[unitId]);
  });
  it.each(removedSelectUnits.filter((unitId) => unitId !== "A2-04"))(
    "%s is absent with its removed selector",
    (unitId) => {
      expect(source(paths[unitId])).not.toContain(bpText[unitId]);
    },
  );
  it("A2-04 has no selector tooltip while A2-05 keeps the shared definition", () => {
    const picker = source(paths["A1-05"]);
    expect(picker).not.toMatch(/<Select(?:\s|>)/);
    expect(picker).not.toContain("handleVersionChange");
    expect(picker).not.toContain("latestPinShown");
    expect(picker.match(/title=\{RECORDED_VERSION_TOOLTIP\}/g)).toHaveLength(1);
    expect(picker).toContain(bpText["A1-10"]);
    expect(source(paths["A2-05"])).toContain(bpText["A2-05"]);
  });
  it("does not promise model execution for composite children", () => {
    const text = source(paths["A3-01"]);
    expect(text).not.toContain("'s saved model when present");
    expect(text).not.toContain("` · v${child.pinned_version_number} model`");
  });
  it("does not promise version creation or recalculation of all past runs", () => {
    const text = source(paths["A4-01"]);
    expect(text).not.toContain("A new version of this metric will be created");
    expect(text).not.toContain("recalculate all past evaluation runs");
  });
  it.each([
    "frontend/src/sections/evals/Helpers/evalUsageColumns.jsx",
    "frontend/src/sections/evals/components/EvalUsageTab.jsx",
    "frontend/src/sections/common/EvalPicker/EvalPickerConfigFull.jsx",
    "frontend/src/sections/evals/components/TestPlayground.jsx",
  ])("preserves shared tooltip wiring in %s", (path) => {
    const text = source(path);
    expect(text).toMatch(/import\s*\{\s*RECORDED_VERSION_TOOLTIP\s*\}\s*from/);
    expect(text).toContain("title={RECORDED_VERSION_TOOLTIP}");
  });
});
