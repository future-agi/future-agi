/* eslint-env node */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { checkGeneratedOutput } from "../../../../scripts/lib/check-generated-output.mjs";

let root;
let outputDir;
let mutatorPath;
let generatedDirectory;
const original = "export const retained = true;\n";
const generatedFile = () => path.join(outputDir, "api.ts");

beforeEach(() => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), "openapi-output-test-"));
  outputDir = path.join(root, "src", "generated", "api-contracts");
  mutatorPath = path.join(root, "src", "api", "contracts", "openapi-mutator.js");
  generatedDirectory = undefined;
  fs.mkdirSync(outputDir, { recursive: true });
  fs.mkdirSync(path.dirname(mutatorPath), { recursive: true });
  fs.writeFileSync(mutatorPath, "export const apiMutator = () => {};\n");
  fs.writeFileSync(generatedFile(), original);
  fs.writeFileSync(path.join(outputDir, "notes.txt"), "keep non-generated content");
});

afterEach(() => fs.rmSync(root, { recursive: true, force: true }));

const check = (generate) => checkGeneratedOutput({
  outputDir,
  mutatorPath,
  generate: async (directory, mutator) => {
    generatedDirectory = directory;
    expect(directory).not.toBe(outputDir);
    expect(path.relative(directory, mutator)).toBe(path.relative(outputDir, mutatorPath));
    expect(fs.readFileSync(mutator, "utf8")).toBe(fs.readFileSync(mutatorPath, "utf8"));
    fs.mkdirSync(directory, { recursive: true });
    await generate(directory);
  },
});

describe("OpenAPI check output isolation", () => {
  it("keeps live clients readable and untouched throughout a successful check", async () => {
    const before = fs.statSync(generatedFile());
    expect(await check(async (directory) => {
      expect(fs.readFileSync(generatedFile(), "utf8")).toBe(original);
      fs.writeFileSync(path.join(directory, "api.ts"), original);
      await Promise.resolve();
      expect(fs.readFileSync(generatedFile(), "utf8")).toBe(original);
    })).toEqual([]);
    const after = fs.statSync(generatedFile());
    expect(after.ino).toBe(before.ino);
    expect(after.mtimeMs).toBe(before.mtimeMs);
    expect(fs.readFileSync(path.join(outputDir, "notes.txt"), "utf8")).toBe("keep non-generated content");
    expect(fs.existsSync(generatedDirectory)).toBe(false);
  });

  it("reports changed, missing and additional clients without replacing source files", async () => {
    fs.writeFileSync(path.join(outputDir, "removed.ts"), "export {};\n");
    const changed = await check(async (directory) => {
      fs.writeFileSync(path.join(directory, "api.ts"), "export const changed = true;\n");
      fs.writeFileSync(path.join(directory, "added.ts"), "export {};\n");
    });
    expect(changed.sort()).toEqual(["added.ts", "api.ts", "removed.ts"]);
    expect(fs.readFileSync(generatedFile(), "utf8")).toBe(original);
    expect(fs.existsSync(path.join(outputDir, "removed.ts"))).toBe(true);
    expect(fs.existsSync(path.join(outputDir, "added.ts"))).toBe(false);
    expect(fs.existsSync(generatedDirectory)).toBe(false);
  });

  it("cleans temporary partial output after failure without touching the live clients", async () => {
    await expect(check(async (directory) => {
      fs.writeFileSync(path.join(directory, "api.ts"), "partial output");
      throw new Error("generation failed");
    })).rejects.toThrow("generation failed");
    expect(fs.readFileSync(generatedFile(), "utf8")).toBe(original);
    expect(fs.existsSync(generatedDirectory)).toBe(false);
  });

  it("preserves and detects edits made while generation is running", async () => {
    const edited = "export const edited = true;\n";
    expect(await check(async (directory) => {
      fs.writeFileSync(path.join(directory, "api.ts"), original);
      fs.writeFileSync(generatedFile(), edited);
    })).toEqual(["api.ts"]);
    expect(fs.readFileSync(generatedFile(), "utf8")).toBe(edited);
  });

  it("reports absent checked-in output without creating it", async () => {
    fs.rmSync(outputDir, { recursive: true });
    expect(await check(async (directory) => {
      fs.writeFileSync(path.join(directory, "api.ts"), original);
    })).toEqual(["api.ts"]);
    expect(fs.existsSync(outputDir)).toBe(false);
    expect(fs.existsSync(generatedDirectory)).toBe(false);
  });
});
