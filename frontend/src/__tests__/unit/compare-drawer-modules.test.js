import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "vitest";

const sourceRoot = fileURLToPath(new URL("../../", import.meta.url));
const projectRoot = path.join(sourceRoot, "sections/project-detail");
const obsoleteModules = ["CompareDrawer", "CompareSection", "AnnotateSection"];

for (const name of obsoleteModules) {
  test(`the unused ${name} module stays removed`, () => {
    assert.equal(
      existsSync(path.join(projectRoot, "CompareDrawer", `${name}.jsx`)),
      false,
      `CompareDrawer/${name}.jsx is obsolete; use CompareDrawer2 instead`,
    );
  });
}

function sourceFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const filename = path.join(directory, entry.name);
    return entry.isDirectory()
      ? sourceFiles(filename)
      : /\.[cm]?[jt]sx?$/.test(entry.name)
        ? [filename]
        : [];
  });
}

test("no source module imports a removed drawer module", () => {
  for (const filename of sourceFiles(sourceRoot)) {
    const source = readFileSync(filename, "utf8");
    for (const match of source.matchAll(
      /(?:\bfrom\s*|\bimport\s*\(?\s*|\brequire\s*\(\s*)["']([^"']+)["']/g,
    )) {
      const specifier = match[1];
      const resolved = specifier.startsWith(".")
        ? path.resolve(path.dirname(filename), specifier)
        : specifier.startsWith("src/")
          ? path.resolve(sourceRoot, specifier.slice(4))
          : null;
      for (const name of obsoleteModules) {
        const removed = path.join(projectRoot, "CompareDrawer", name);
        assert.notEqual(resolved?.replace(/\.jsx?$/, ""), removed, filename);
      }
    }
  }
});

test("the project uses the active drawer and keeps its shared modules", () => {
  assert.match(
    readFileSync(path.join(projectRoot, "ProjectDetailView.jsx"), "utf8"),
    /from ["']\.\/CompareDrawer2\/CompareDrawer["']/,
  );
  for (const name of [
    "CompareNavigateButtons",
    "SelectRunsPopover",
    "SelectEvalPopover",
    "StatusCellRenderer",
    "TraceDataList",
    "CompareExperimentRightSection",
    "CompareEvalGrid",
    "NoRowsOverlay",
    "RunTraceTree",
    "Skeletons/CompareDrawerSkeleton",
  ]) {
    assert.ok(
      existsSync(path.join(projectRoot, "CompareDrawer", `${name}.jsx`)),
      `Shared module ${name} must remain available`,
    );
  }
});
