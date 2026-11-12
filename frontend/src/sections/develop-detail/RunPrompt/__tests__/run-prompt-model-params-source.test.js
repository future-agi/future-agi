// Restore row 3: Dataset > Add column > Run Prompt > Model options shows every
// parameter the chosen model offers, as production (origin/main 24cc6576)
// does: Top P, presence and frequency penalty, and every on/off and dropdown
// option. The launch build filtered them to Temperature and Max tokens.
// The server stores and uses them: serializers/run_prompt.py accepts
// run_prompt_config as free JSON, views/run_prompt.py reads top_p and the
// penalties from it and passes them to every cell's model call.
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const moduleUrl = new URL(import.meta.url);
const sourceRelativePath = "src/sections/develop-detail/RunPrompt/RunPrompt.jsx";
const cwdSourcePath = resolve(process.cwd(), sourceRelativePath);
const sourcePath =
  moduleUrl.protocol === "file:"
    ? resolve(dirname(fileURLToPath(moduleUrl)), "../RunPrompt.jsx")
    : existsSync(cwdSourcePath)
      ? cwdSourcePath
      : resolve(process.cwd(), "frontend", sourceRelativePath);
const SOURCE = readFileSync(sourcePath, "utf8");
const squash = (text) => text.replace(/\s+/g, " ");

const slice = (start, end) => {
  const from = SOURCE.indexOf(start);
  const to = from < 0 ? -1 : SOURCE.indexOf(end, from + start.length);
  return from < 0 || to < 0 ? "" : squash(SOURCE.slice(from, to));
};

describe("Run Prompt model options are not filtered", () => {
  it("shows the model's reasoning settings (Reasoning effort, Show reasoning process)", () => {
    // Production passes them (origin/main RunPrompt.jsx); the server honours
    // both (run_prompt_config.reasoning, sent at RunPrompt.jsx:158-163).
    expect(SOURCE).not.toMatch(/reasoning=\{undefined\}/);
    expect(SOURCE).toMatch(/reasoning=\{modelParams\?\.reasoning\}/);
  });

  it("has no parameter filter", () => {
    expect(SOURCE).not.toMatch(/honouredModelParams|HONOURED_PARAMS/);
  });

  it("opens the options with every parameter the model offers", () => {
    const open = slice(
      "const handleOpenModelParams = (e) => {",
      "const handleApplyModelParams",
    );
    expect(open).toContain(
      "setDraftModelParameters(JSON.parse(JSON.stringify(modelParameters)));",
    );
  });

  it("Clear resets every slider, on/off and dropdown to its default", () => {
    const clear = slice(
      "const handleClearModelParams = () => {",
      "setDraftReasoningState(getDefaultReasoningState(",
    );
    expect(clear).toContain("...modelParameters,");
    expect(clear).toContain("sliders: modelParameters.sliders.map((s) => ({");
    expect(clear).toContain("booleans: modelParameters.booleans.map((b) => ({");
    expect(clear).toContain(
      "dropdowns: modelParameters.dropdowns.map((d) => ({",
    );
  });

  it("Apply is enabled only by a real change", () => {
    const changed = slice(
      "const hasModelParamsChanges = useMemo(() => {",
      "}, [",
    );
    expect(changed).toContain(
      "!_.isEqual(draftModelParameters, modelParameters) ||",
    );
  });
});
