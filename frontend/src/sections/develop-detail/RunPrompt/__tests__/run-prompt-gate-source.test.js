// R1-02 wiring: RunPrompt.jsx must not gate runs on a hard-coded model and
// provider-key status (it refused gpt-4o-mini unless OpenAI had a key, on
// edit/run and preview but not create). Every path that starts a run (create,
// edit/run, preview) awaits the managed-or-BYOK eligibility gate first and
// returns before any request when it refuses. The request bodies themselves
// are unchanged, so BYOK runs send the same bytes as before.
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// Vite/jsdom may expose an HTTP import.meta.url. Only file: URLs can be
// converted to disk paths; otherwise use the frontend (or repository) cwd.
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
const GATE = "await passesExecutionGate(submitObject)";
const REFUSED_RETURNS =
  /if \(!\(await passesExecutionGate\(submitObject\)\)\) return;/g;

const slice = (start, end) => {
  const from = SOURCE.indexOf(start);
  const to = from < 0 ? -1 : SOURCE.indexOf(end, from + start.length);
  return from < 0 || to < 0 ? "" : SOURCE.slice(from, to);
};
const ON_SUBMIT = slice(
  "const onSubmit = async (data) => {",
  "const onSubmitPreview = ",
);
const ON_PREVIEW = slice(
  "const onSubmitPreview = async (data, testData) => {",
  "const handleIncrease = ",
);

describe("Run Prompt has no hard-coded provider-key gate", () => {
  it("names no model, reads no provider-key status, shows no OpenAI-only copy", () => {
    expect(SOURCE).not.toMatch(/gpt-4o-mini/);
    expect(SOURCE).not.toMatch(/useApiKeysStatus|apiKeysStatus|hasKey/);
    expect(SOURCE).not.toMatch(/performExtraCheckForSampleDataset/);
    expect(SOURCE).not.toContain("Please configure OpenAI API key");
  });

  it("uses the shared eligibility gate", () => {
    expect(SOURCE).toContain(
      'import { useExecutionEligibilityGate } from "./executionEligibility";',
    );
    expect(SOURCE).toContain(
      "const passesExecutionGate = useExecutionEligibilityGate();",
    );
  });
});

describe("create, edit/run and preview run only after the gate", () => {
  it("both submit handlers can wait for the gate", () => {
    expect(ON_SUBMIT).not.toBe("");
    expect(ON_PREVIEW).not.toBe("");
  });

  it("edit/run: inside the edit branch, before updateRunPrompt", () => {
    const branch = ON_SUBMIT.indexOf("if (isEditMode) {");
    const call = ON_SUBMIT.indexOf("updateRunPrompt(");
    expect(branch).toBeGreaterThan(-1);
    expect(call).toBeGreaterThan(branch);
    expect(ON_SUBMIT.lastIndexOf(GATE, call)).toBeGreaterThan(branch);
  });

  it("create: inside the create branch, before createRunPrompt", () => {
    const update = ON_SUBMIT.indexOf("updateRunPrompt(");
    const branch = ON_SUBMIT.indexOf("} else {", update);
    const call = ON_SUBMIT.indexOf("createRunPrompt(submitObject);");
    expect(branch).toBeGreaterThan(update);
    expect(call).toBeGreaterThan(branch);
    expect(ON_SUBMIT.lastIndexOf(GATE, call)).toBeGreaterThan(branch);
  });

  it("preview: after variable validation, before previewRunPrompt", () => {
    const validated = ON_PREVIEW.indexOf("if (inValidVariables?.length > 0) {");
    const call = ON_PREVIEW.indexOf("previewRunPrompt(submitObject);");
    expect(validated).toBeGreaterThan(-1);
    expect(call).toBeGreaterThan(validated);
    expect(ON_PREVIEW.lastIndexOf(GATE, call)).toBeGreaterThan(validated);
  });

  it("a refusal returns before any request, on all three paths", () => {
    expect([...SOURCE.matchAll(REFUSED_RETURNS)]).toHaveLength(3);
  });

  it("the conditional-node hand-off (onFormSubmit) starts no run and is not gated", () => {
    const handOff = ON_SUBMIT.indexOf("onFormSubmit(submitObject);");
    expect(handOff).toBeGreaterThan(-1);
    expect(handOff).toBeLessThan(ON_SUBMIT.indexOf(GATE));
  });
});

describe("BYOK requests are byte-for-byte the same", () => {
  it.each([
    "createRunPrompt(submitObject);",
    "previewRunPrompt(submitObject);",
    "updateRunPrompt( withRerunScope( { column_id: editConfigData?.id, ...submitObject }, rerunScope, EDIT_RUN_PROMPT_BODY, ), );",
    "mutationFn: (data) => axios.post(endpoints.develop.runPrompt.create, data),",
    "mutationFn: (d) => axios.post(endpoints.develop.runPrompt.preview, d),",
    "mutationFn: (data) => axios.post(endpoints.develop.runPrompt.editRunPrompt(), data),",
  ])("keeps %s", (expression) => {
    expect(squash(SOURCE)).toContain(expression);
  });
});
