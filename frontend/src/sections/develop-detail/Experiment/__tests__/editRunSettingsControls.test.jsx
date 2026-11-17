// On Edit, the wizard's run-setting controls follow the edit contract: the
// error policy, the metadata rows and each run's limits can be changed where
// the contract declares them, and stay read-only where it does not. The
// sample shows what the experiment was created with, read-only and with the
// reason, in every build: it is never hidden.
import React from "react";
import PropTypes from "prop-types";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { afterEach, describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import { useForm } from "react-hook-form";
import { render } from "src/utils/test-utils";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import RowLimits from "src/sections/common/RunSettings/RowLimits";
import { ROW_LIMIT_FIELDS } from "src/sections/common/RunSettings/rowLimitsModel";
import SampleSetting from "../StepperComponentExperiment/SampleSetting";
import { emptyExperimentValues } from "../experimentFormValues";
import { SAMPLE_FIXED_TEXT } from "../editRunSettings";

const HELD = {
  launch: true,
  experimentsBasic: true,
  madeReal: [],
  showUnreleased: false,
};

afterEach(() => setLaunchOverrideForTest(null));

function Host({ defaults = {}, children }) {
  const form = useForm({
    defaultValues: { ...emptyExperimentValues(), ...defaults },
  });
  return children(form);
}
Host.propTypes = { defaults: PropTypes.object, children: PropTypes.func };

const sample = (defaults, props = {}) =>
  render(
    <Host defaults={defaults}>
      {(f) => <SampleSetting control={f.control} inScope={8} {...props} />}
    </Host>,
  );

const rowLimits = (limits, props = {}) =>
  render(
    <Host defaults={{ promptConfig: [{ rowLimits: limits }] }}>
      {(f) => (
        <RowLimits
          control={f.control}
          fieldPrefix="promptConfig.0"
          {...props}
        />
      )}
    </Host>,
  );

describe("the sample on Edit", () => {
  it("shows the stored sample read-only, with the reason it is fixed", () => {
    sample(
      { sample: { on: true, n: 2, seed: 17 } },
      { fixedText: SAMPLE_FIXED_TEXT },
    );
    const box = screen.getByTestId("sample-setting");
    expect(within(box).getByRole("checkbox").checked).toBe(true);
    expect(within(box).getByRole("checkbox").disabled).toBe(true);
    expect(within(box).getByLabelText("Rows in the sample").disabled).toBe(
      true,
    );
    expect(within(box).getByLabelText("Sample seed").disabled).toBe(true);
    expect(box.textContent).toContain("2 random rows (seed 17) of 8");
    expect(box.textContent).toContain(SAMPLE_FIXED_TEXT);
  });

  it("shows while the launch build hides the sample on a new experiment", () => {
    setLaunchOverrideForTest(HELD);
    const { unmount } = sample({});
    expect(screen.queryByTestId("sample-setting")).toBeNull();
    unmount();
    sample({}, { fixedText: SAMPLE_FIXED_TEXT });
    const box = screen.getByTestId("sample-setting");
    expect(within(box).getByRole("checkbox").checked).toBe(false);
    expect(within(box).getByRole("checkbox").disabled).toBe(true);
    expect(box.textContent).toContain("Runs every row in scope.");
    expect(box.textContent).toContain(SAMPLE_FIXED_TEXT);
  });

  it("says the server's own sentence", () => {
    expect(SAMPLE_FIXED_TEXT).toBe(
      "An experiment's rows are chosen when it is created, so its sample can't change. Create a new experiment to sample other rows.",
    );
  });
});

describe("each run's limits", () => {
  it("are read-only when the Edit cannot save them", () => {
    rowLimits({ timeout_s: 30 }, { disabled: true });
    ROW_LIMIT_FIELDS.forEach(({ key }) =>
      expect(screen.getByTestId(`row-limit-${key}`).disabled).toBe(true),
    );
    expect(screen.getByTestId("row-limit-timeout_s").value).toBe("30");
  });

  it("can be changed otherwise", () => {
    rowLimits({ timeout_s: 30 });
    ROW_LIMIT_FIELDS.forEach(({ key }) =>
      expect(screen.getByTestId(`row-limit-${key}`).disabled).toBe(false),
    );
  });
});

// The steps pass the contract's answer to each control.
const source = (path) =>
  readFileSync(
    resolve(process.cwd(), "src/sections/develop-detail/Experiment", path),
    "utf8",
  );

describe("the Edit steps wire each control to the edit contract", () => {
  it("Review: the error policy and the metadata rows", () => {
    const review = source("StepperComponentExperiment/ReviewStep.jsx");
    expect(review).toMatch(
      /const lockedOn = \{ isEditing, base: values\.runSettingsBase \};/,
    );
    expect(review).toMatch(
      /const policyLocked = editSettingLocked\("error_policy", lockedOn\);/,
    );
    expect(review).toMatch(
      /const metadataLocked = editSettingLocked\("metadata", lockedOn\);/,
    );
    expect(review).toMatch(
      /<ErrorPolicySetting\s+control=\{control\}\s+disabled=\{policyLocked\}/,
    );
    expect(review).not.toMatch(
      /<ErrorPolicySetting[^>]*disabled=\{isEditing\}/,
    );
  });

  it("Configuration: each run's limits", () => {
    const card = source("StepperComponentExperiment/AgentPromptRenderer.jsx");
    expect(card).toMatch(
      /const settingsBase = useWatch\(\{ control, name: "runSettingsBase" \}\);/,
    );
    expect(card).toMatch(
      /const limitsLocked = editSettingLocked\("limits", \{\s*isEditing,\s*base: settingsBase,\s*\}\);/,
    );
    expect(card).toMatch(
      /<RowLimits\s+control=\{control\}\s+fieldPrefix=\{fieldPrefix\}\s+disabled=\{limitsLocked\}/,
    );
  });

  it("Data: the sample, fixed and shown on every Edit", () => {
    const data = source("StepperComponentExperiment/DataStep.jsx");
    expect(data).toMatch(/\{!rowIds\.length \|\| isEditing \? \(/);
    expect(data).toMatch(/fixedText=\{isEditing \? SAMPLE_FIXED_TEXT : null\}/);
  });

  it("the Edit form opens with the experiment's stored settings", () => {
    const form = source("RunExperiment.jsx");
    // Edit still restores stored settings; only a copy takes the source's
    // dataset selection before falling back to a blank form's built values.
    expect(form).toMatch(
      /isEditing\s*\?\s*withStoredRunSettings\(builtValues, sourceDetail\)\s*:\s*sourceDetail\s*\?\s*\{\s*\.\.\.builtValues,\s*\.\.\.experimentDatasetSelection\(sourceDetail,\s*\{\s*userEvalMetrics: builtValues\.userEvalMetrics,\s*\}\),\s*\}\s*:\s*builtValues/,
    );
  });
});
