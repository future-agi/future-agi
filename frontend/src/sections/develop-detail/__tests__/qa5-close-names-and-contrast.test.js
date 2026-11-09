import { readFileSync } from "node:fs";
import { expect, it } from "vitest";
const source = (relative) =>
  readFileSync(new URL(relative, import.meta.url), "utf8");

it.each([
  "../../develop/AddDatasetDrawer/UploadFileModal.jsx",
  "../DataTab/EditColumnName.jsx",
  "../DataTab/EditColumnType.jsx",
  "../DataTab/DeleteColumn.jsx",
  "../AddColumn/AddColumnDrawer.jsx",
  "../AddColumn/AddColumnApiCall/AddColumnApiCall.jsx",
  "../AddColumn/Retrieval/Retrieval.jsx",
  "../AddColumn/ExtractEntities/ExtractEntities.jsx",
  "../AddColumn/Classification/Classification.jsx",
  "../RunPrompt/RunPrompt.jsx",
  "../../common/EvaluationDrawer/EvaluationDrawer.jsx",
])("keeps icon-only close controls named in %s", (file) => {
  const controls = [
    ...source(file).matchAll(/<IconButton\b([\s\S]*?)<\/IconButton>/g),
  ].filter(([markup]) =>
    /icon="(?:[^"]*close[^"]*|akar-icons:cross)"/.test(markup),
  );
  expect(controls.length).toBeGreaterThan(0);
  for (const [markup] of controls)
    expect(markup).toMatch(/aria-label="(?:Close [^"]+|Remove file)"/);
});

const luminance = (hex) => {
  const rgb = hex
    .replace("#", "")
    .match(/../g)
    .map((c) => parseInt(c, 16) / 255)
    .map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
};
it("uses DS tokens whose enabled foregrounds meet AA on light surfaces", () => {
  const tokens = source("../../../theme/tokens.css");
  const color = (name) =>
    tokens.match(new RegExp(`--ds-${name}: (#[0-9A-Fa-f]{6})`))[1];
  const bg = luminance(color("bg-surface"));
  for (const name of ["status-danger-fg", "fg-default"]) {
    const fg = luminance(color(name));
    expect(
      (Math.max(bg, fg) + 0.05) / (Math.min(bg, fg) + 0.05),
    ).toBeGreaterThanOrEqual(4.5);
  }
  expect(source("../DataTab/common.js")).toContain(
    "var(--ds-status-danger-fg)",
  );
  expect(source("../AddColumn/AddColumnDrawer.jsx")).toContain(
    "var(--ds-fg-default)",
  );
});
