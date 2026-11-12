import React from "react";
import PropTypes from "prop-types";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { useForm } from "react-hook-form";
import { render } from "src/utils/test-utils";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { emptyExperimentValues } from "../experimentFormValues";
import ConfigureStepExperimentCreation from "../StepperComponentExperiment/ConfigureStepExperimentCreation";

vi.mock("../StepperComponentExperiment/ConfigureStepLLM", () => ({
  default: () => null,
}));
vi.mock("../StepperComponentExperiment/ConfigureStepTTS", () => ({
  default: () => null,
}));
vi.mock("../StepperComponentExperiment/ConfigureStepSTT", () => ({
  default: () => null,
}));
vi.mock("../StartFromExperiment", () => ({ default: () => null }));

afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
});
function Host({ runs, onFocus }) {
  const form = useForm({
    defaultValues: { ...emptyExperimentValues(), promptConfig: runs },
  });
  return (
    <ConfigureStepExperimentCreation
      control={form.control}
      getValues={form.getValues}
      setValue={form.setValue}
      unregister={form.unregister}
      clearErrors={form.clearErrors}
      watch={form.watch}
      errors={{}}
      nameState={{ onFocus }}
    />
  );
}
Host.propTypes = { runs: PropTypes.array, onFocus: PropTypes.func };

describe("configuration name focus", () => {
  it.each([
    { runs: [] },
    { runs: [{ targetType: "agent", agentId: "graph", name: "Saved graph" }] },
  ])("signals editing before text entry with runs $runs", ({ runs }) => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: true,
      madeReal: [],
    });
    const onFocus = vi.fn();
    render(<Host runs={runs} onFocus={onFocus} />);
    fireEvent.focus(screen.getByRole("textbox", { name: /^Experiment name/ }));
    expect(onFocus).toHaveBeenCalledOnce();
  });
});
