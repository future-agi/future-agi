/* eslint-env node */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { setLaunchOverrideForTest } from "../launchSwitch";
import { madeRealSurfaceIds } from "../madeReal";
import { experimentBody } from "src/sections/develop-detail/Experiment/experimentBody";
import { classicExperimentEvalSelection } from "../experimentExecutionPin";

// Read source, not Vite's client asset-URL glob (which misses nested paths).
const read = (path) =>
  readFileSync(resolve(process.cwd(), "src", path), "utf8");
const launch = (experimentsBasic) =>
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic,
    madeReal: madeRealSurfaceIds(),
  });
afterEach(() => setLaunchOverrideForTest(null));

// J09 REVISED: mounting the classic vehicle must not enable a NEW child.
describe("classic experiment child isolation", () => {
  it.each(["v3", "latest"])(
    "does not persist a new execution pin (%s) from cached wizard config",
    (pin) => {
      launch(false);
      const metric = {
        template_id: "eval-1",
        name: "quality",
        pinned_version_id: "version-3",
        config: {
          version_pin: pin,
          mapping: { output: "output" },
          pass_threshold: 0.7,
        },
      };
      const body = experimentBody(
        { experimentType: "llm", promptConfig: [], userEvalMetrics: [metric] },
        { promptConfig: [] },
        { datasetId: "d1" },
      );
      expect(body.user_eval_metrics).toEqual([
        {
          template_id: "eval-1",
          name: "quality",
          config: { mapping: { output: "output" }, pass_threshold: 0.7 },
        },
      ]);
      expect(metric.config.version_pin).toBe(pin);
      expect(metric.pinned_version_id).toBe("version-3");
    },
  );

  it("retains pins when the existing NEW creation surface is enabled", () => {
    launch(true);
    const metric = { template_id: "eval-1", config: { version_pin: "v3" } };
    const body = experimentBody(
      { experimentType: "llm", promptConfig: [], userEvalMetrics: [metric] },
      { promptConfig: [] },
      { datasetId: "d1" },
    );
    expect(body.user_eval_metrics).toEqual([metric]);
  });

  it.each(["version-3", null])(
    "preserves the reconciled classic binding pin (%s) without mutating it",
    (pin) => {
      launch(false);
      const bindingPayload = Object.freeze({
        template_id: "eval-1",
        pinned_version_id: pin,
        config: Object.freeze({ mapping: { output: "changed-column" } }),
      });
      const payload = Object.freeze({
        userEvalId: "binding-1",
        versionId: pin,
        bindingPayload,
      });
      expect(classicExperimentEvalSelection(payload, "experiment")).toBe(
        payload,
      );
      expect(bindingPayload.pinned_version_id).toBe(pin);
    },
  );

  it("leaves deliberate definition-edit pin removal to the binding reconciler", () => {
    launch(false);
    const payload = {
      userEvalId: "binding-1",
      versionId: undefined,
      bindingPayload: {
        template_id: "eval-1",
        config: { config: { rule_prompt: "Edited {{output}}" } },
      },
    };
    expect(classicExperimentEvalSelection(payload, "experiment")).toBe(payload);
    expect(payload.bindingPayload).not.toHaveProperty("pinned_version_id");
  });

  it.each(["v3", "latest"])(
    "still strips a new classic execution pin (%s), not the selected config",
    (pin) => {
      launch(false);
      const payload = {
        templateId: "eval-1",
        pinned_version_id: "version-3",
        versionId: "version-3",
        versionPin: pin,
        config: { version_pin: pin, rule_prompt: "Saved {{output}}" },
      };
      expect(classicExperimentEvalSelection(payload, "experiment")).toEqual({
        templateId: "eval-1",
        config: { rule_prompt: "Saved {{output}}" },
      });
      expect(payload.versionId).toBe("version-3");
      expect(payload.config.version_pin).toBe(pin);
      expect(classicExperimentEvalSelection(payload, "dataset")).toBe(payload);
      launch(true);
      expect(classicExperimentEvalSelection(payload, "experiment")).toBe(
        payload,
      );
    },
  );

  it("keeps the classic evaluation drawer out of every W1-2 re-score entry", () => {
    const host = read(
      "sections/experiment-detail-classic/ExperimentData/ExperimentDataView.jsx",
    );
    expect(host).toMatch(/<EvaluationDrawer[\s\S]*?allowRescore=\{false\}/);
    const drawer = read(
      "sections/common/EvaluationDrawer/EvaluationDrawer.jsx",
    );
    expect(drawer).toMatch(/const rescoreShown = isExperiment && allowRescore/);
    expect(drawer).toMatch(/\{rescoreShown \? rescore\.dialog : null\}/);
    expect(drawer).toMatch(
      /onRunEvalsClick=\{\s*rescoreShown \? rescoreEvals : undefined\s*\}/,
    );
    expect(drawer).toMatch(/if \(rescoreShown && payload\.run !== false\)/);
    expect(drawer).toMatch(
      /if \(rescoreShown && !payload\.user_eval_id && payload\.run\)/,
    );
    // The classic direct run and experiment-scoped group application survive.
    expect(drawer).toMatch(/axios\.post\(runEndpoint, runPayload\)/);
    expect(drawer).toContain('data["filters"]["experiment_id"] = experimentId');
  });

  it("gates the new judge drafting and inline card test doors", () => {
    const evaluation = read(
      "sections/develop-detail/Experiment/StepperComponentExperiment/EvaluationStepExperimentCreation.jsx",
    );
    expect(evaluation).toMatch(
      /isSurfaceShown\("exp\.new"\) && referenceColumn \?/,
    );
    expect(evaluation).toMatch(
      /isSurfaceShown\("exp\.new"\) && \(\s*<DraftJudgeDialog/,
    );
    expect(evaluation).toMatch(
      /isSurfaceShown\("exp\.new"\) && \(\s*<CustomTooltip[\s\S]*?setTestOpen/,
    );
    expect(evaluation).toMatch(/isSurfaceShown\("exp\.new"\) && testOpen/);
  });

  it("keeps new-run tracking gated without hiding the retained tray", () => {
    const form = read("sections/develop-detail/Experiment/RunExperiment.jsx");
    expect(form).toMatch(
      /undo:\s*isSurfaceShown\("dataset\.tab\.experiments"\) && r\.experimentId/,
    );
    expect(form).toMatch(
      /if \(isSurfaceShown\("dataset\.tab\.experiments"\) && r\.runs\?\.length\)/,
    );
    expect(form).toMatch(
      /isSurfaceShown\("dataset\.tab\.experiments"\) &&\s*\(?\s*<RunTrayDock/,
    );
    const tray = read("sections/develop-detail/Experiment/RunTray.jsx");
    expect(tray).not.toContain('isSurfaceShown("dataset.tab.experiments")');
    expect(tray).toContain('const canOpen = isSurfaceShown("exp.page");');
  });
});
