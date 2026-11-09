import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import ts from "typescript";
import { describe, expect, it } from "vitest";

const root = process.cwd();
const readJson = (file) =>
  JSON.parse(fs.readFileSync(path.join(root, file), "utf8"));

describe("frontend validation configuration", () => {
  it("runs the compiler without a missing-config success fallback", () => {
    expect(readJson("package.json").scripts["type-check"]).toBe(
      "tsc --noEmit --project tsconfig.json",
    );
  });

  it("checks JavaScript product code, test files, fixtures, and build checks", () => {
    const loaded = ts.readConfigFile(
      path.join(root, "tsconfig.json"),
      ts.sys.readFile,
    );
    expect(loaded.error).toBeUndefined();
    const config = ts.parseJsonConfigFileContent(loaded.config, ts.sys, root);
    expect(config.errors).toEqual([]);
    expect(config.options.allowJs).toBe(true);
    expect(config.options.checkJs).toBe(true);
    expect(config.options.noEmit).toBe(true);
    const files = config.fileNames.map((file) =>
      path.relative(root, file).replaceAll("\\", "/"),
    );
    expect(files).toEqual(
      expect.arrayContaining([
        "src/utils/seededRandom.js",
        "src/sections/develop-detail/Experiment/reviewModel.js",
        "src/sections/common/Lenses/__tests__/lensModel.test.js",
        "src/sections/common/Values/__tests__/k4f-b-schema-wrapped.test.js",
        "src/test-fixtures/voice-calls.js",
        "src/test-fixtures/wrapped-conversations.js",
        "scripts/public-bundle-plugin.mjs",
        "scripts/public-bundle-isolation.mjs",
        "src/__tests__/unit/public-bundle-isolation.test.js",
      ]),
    );
  });

  it("exposes the actual built-dist checker as a package command", () => {
    expect(readJson("package.json").scripts["check:public-bundle"]).toBe(
      "node scripts/public-bundle-isolation.mjs dist",
    );
  });

  it("disables automatic dotenv loading in the test configuration", () => {
    const source = ts.createSourceFile(
      "vitest.config.js",
      fs.readFileSync(path.join(root, "vitest.config.js"), "utf8"),
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.JS,
    );
    const declarations = [];
    const visit = (node) => {
      if (
        ts.isPropertyAssignment(node) &&
        node.name.getText(source) === "envDir"
      ) {
        declarations.push(node.initializer.kind);
      }
      ts.forEachChild(node, visit);
    };
    visit(source);
    expect(declarations).toEqual([ts.SyntaxKind.FalseKeyword]);
  });
});
