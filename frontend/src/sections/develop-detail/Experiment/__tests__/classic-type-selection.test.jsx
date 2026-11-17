/* eslint-disable react/prop-types */
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { useForm, useWatch } from "react-hook-form";
import { afterEach, expect, it, vi } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";
vi.mock("../StepperComponentExperiment/ConfigureStepLLM", () => ({
  default: () => <div>Classic prompt configuration</div>,
}));
vi.mock("../StepperComponentExperiment/ConfigureStepTTS", () => ({
  default: () => <div>Classic media configuration</div>,
}));
vi.mock("../StepperComponentExperiment/ConfigureStepSTT", () => ({
  default: () => <div>Classic transcription configuration</div>,
}));
vi.mock("../StartFromExperiment", () => ({
  default: () => <div data-testid="new-start-from" />,
}));
vi.mock("src/sections/common/ux/oneview", () => ({
  Section: ({ children }) => children,
  useLayoutPrefs: () => [{}, vi.fn()],
}));
import ConfigureStep from "../StepperComponentExperiment/ConfigureStepExperimentCreation";
function Form() {
  const form = useForm({
    defaultValues: {
      name: "Classic",
      experimentType: "llm",
      promptConfig: [],
      outputFormat: "string",
    },
  });
  const kind = useWatch({ control: form.control, name: "experimentType" });
  return (
    <>
      <ConfigureStep {...form} allColumns={[]} />
      <output aria-label="selected experiment type">{kind}</output>
    </>
  );
}
afterEach(() => setLaunchOverrideForTest(null));
it("offers and applies legacy experiment types without More settings, record or start-from", () => {
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: false,
    madeReal: madeRealSurfaceIds(),
  });
  render(<Form />);
  fireEvent.mouseDown(
    screen.getByRole("combobox", { name: "Experiment type" }),
  );
  for (const name of ["LLM", "TTS", "STT", "Image"])
    expect(
      screen.getByRole("option", { name: new RegExp(`^${name}`) }),
    ).toBeVisible();
  fireEvent.click(screen.getByRole("option", { name: /^STT/ }));
  expect(screen.getByLabelText("selected experiment type")).toHaveTextContent(
    "stt",
  );
  expect(screen.getByText("Classic transcription configuration")).toBeVisible();
  expect(screen.queryByTestId("new-start-from")).toBeNull();
  expect(screen.queryByText("More settings")).toBeNull();
  expect(screen.queryByText("Record")).toBeNull();
});
