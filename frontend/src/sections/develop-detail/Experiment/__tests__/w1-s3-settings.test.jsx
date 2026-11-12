// W1-6 / W1-8: the Data step's seeded sample and Review's "Score each row
// from its tries" set the form values the create body sends.
import React from "react";
import PropTypes from "prop-types";
import { describe, expect, it } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import { useForm } from "react-hook-form";
import { render } from "src/utils/test-utils";
import SampleSetting from "../StepperComponentExperiment/SampleSetting";
import TrialReducerSetting from "../StepperComponentExperiment/TrialReducerSetting";
import { experimentExtras } from "../experimentFormValues";

let lastValues = null;
function Host({ children }) {
  const { control, watch } = useForm({ defaultValues: { name: "x" } });
  lastValues = watch();
  return children(control);
}
Host.propTypes = { children: PropTypes.func };

describe("W1 S3 · settings", () => {
  it("A random [25] rows · seed [7] reaches the create body", () => {
    render(
      <Host>
        {(control) => <SampleSetting control={control} inScope={248} />}
      </Host>,
    );
    const box = screen.getByTestId("sample-setting");
    expect(box.textContent).toContain("Runs every row in scope.");
    fireEvent.click(within(box).getByRole("checkbox"));
    fireEvent.change(within(box).getByLabelText("Rows in the sample"), {
      target: { value: "25" },
    });
    fireEvent.change(within(box).getByLabelText("Sample seed"), {
      target: { value: "7" },
    });
    expect(box.textContent).toContain(
      "25 random rows (seed 7) of 248. The same seed picks the same rows again.",
    );
    expect(experimentExtras(lastValues).sample).toEqual({ n: 25, seed: 7 });
  });

  it("Score each row from its tries: pass^3 reaches the create body", () => {
    render(
      <Host>
        {(control) => <TrialReducerSetting control={control} tries={3} />}
      </Host>,
    );
    const box = screen.getByTestId("setting-trialReducer");
    fireEvent.mouseDown(within(box).getByRole("combobox"));
    const names = screen.getAllByRole("option").map((o) => o.textContent);
    expect(names).toEqual([
      "Mean",
      "Median",
      "Most common",
      "Best",
      "pass@3",
      "pass^3",
      "At least n of 3",
    ]);
    fireEvent.click(screen.getByRole("option", { name: "pass^3" }));
    expect(experimentExtras(lastValues).trial_reducer).toEqual({
      kind: "pass_hat_k",
    });
    expect(box.textContent).toContain(
      "A row passes only when every try passes.",
    );
  });
});
