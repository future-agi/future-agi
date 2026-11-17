// When Run experiment sends nothing, a plain line by the button
// says why (RunExperiment.jsx `submit-refusal`), instead of only a short toast.
import { describe, expect, it } from "vitest";
import { EXPERIMENT_STEP, experimentCreationSteps } from "../constants";
import {
  SETTINGS_NEED_ATTENTION,
  refusalLine,
  refusalOfBlocks,
  refusalOfErrors,
} from "../submitRefusal";

const RUN_LINE =
  "The run “Answer graph” has a setting this form can't read. Remove the run and add it again.";

describe("Why Run sent nothing, in plain words", () => {
  it("turns the check's own wording into a line that names the run", () => {
    const errors = {
      promptConfig: [
        undefined,
        {
          versionLabel: {
            type: "invalid_type",
            message: "Expected string, received number",
          },
        },
      ],
    };
    const values = {
      promptConfig: [{ name: "Answer a question" }, { name: "Answer graph" }],
    };
    const refusal = refusalOfErrors(errors, values);
    expect(refusal).toEqual({
      step: EXPERIMENT_STEP.CONFIGURATION,
      text: RUN_LINE,
    });
    const line = refusalLine(refusal, experimentCreationSteps);
    expect(line).toBe(`Not ready: Configuration · ${RUN_LINE}`);
    expect(line).not.toMatch(/Expected|received|invalid_type/);
  });

  it("keeps the form's own written messages", () => {
    expect(
      refusalOfErrors({
        userEvalMetrics: {
          type: "too_small",
          message: "Add at least one evaluation",
        },
      }),
    ).toEqual({
      step: EXPERIMENT_STEP.EVALUATIONS,
      text: "Add at least one evaluation",
    });
  });

  it("names a setting whose check failed without words", () => {
    expect(
      refusalOfErrors({
        maxConcurrency: {
          type: "too_big",
          message: "Number must be less than or equal to 50",
        },
      }),
    ).toEqual({
      step: EXPERIMENT_STEP.REVIEW,
      text: "Rows at once is not valid. Check it and try again.",
    });
  });

  it("still says something when the check names no field", () => {
    expect(refusalOfErrors({})).toEqual({
      step: EXPERIMENT_STEP.REVIEW,
      text: SETTINGS_NEED_ATTENTION,
    });
  });

  it("shows a server block only the submitted body carries", () => {
    const issues = [[], [], [], ["Trials must be 1–10"]];
    const listed = {
      step: EXPERIMENT_STEP.REVIEW,
      text: "Trials must be 1–10",
    };
    const generic = {
      text: "config isn't supported here. Remove it to run the experiment.",
    };
    expect(refusalOfBlocks([listed], issues)).toBeNull();
    expect(refusalOfBlocks([listed, generic], issues)).toEqual({
      step: EXPERIMENT_STEP.REVIEW,
      text: generic.text,
    });
  });
});
