/* eslint-disable react/prop-types */
// The New experiment wizard offers every experiment type the backend runs
// (LLM, TTS, STT, Image), the same four the classic wizard offers, and
// picking one opens that type's configuration.
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { useForm, useWatch } from "react-hook-form";
import { describe, expect, it, vi } from "vitest";
import { isSurfaceShown } from "src/launch/launchSwitch";

vi.mock("../StepperComponentExperiment/ConfigureStepLLM", () => ({
  default: () => <div>Prompt configuration</div>,
}));
vi.mock("../StepperComponentExperiment/ConfigureStepTTS", () => ({
  default: () => <div>Media configuration</div>,
}));
vi.mock("../StepperComponentExperiment/ConfigureStepSTT", () => ({
  default: () => <div>Transcription configuration</div>,
}));
vi.mock("../StartFromExperiment", () => ({
  default: () => <div data-testid="new-start-from" />,
}));
vi.mock("src/sections/common/ux/oneview", () => ({
  Section: ({ children }) => children,
  useLayoutPrefs: () => [{}, vi.fn()],
}));

import ConfigureStep from "../StepperComponentExperiment/ConfigureStepExperimentCreation";
import { moreSettingsChanges } from "../moreSettings";

function Form() {
  const form = useForm({
    defaultValues: {
      name: "New",
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

const openTypePicker = () =>
  fireEvent.mouseDown(
    screen.getByRole("combobox", { name: "Experiment type" }),
  );

describe("New experiment: experiment type", () => {
  it("is the new wizard (More settings holds the type picker)", () => {
    expect(isSurfaceShown("exp.new")).toBe(true);
    render(<Form />);
    expect(screen.getByTestId("new-experiment-more")).toBeInTheDocument();
  });

  it("offers LLM, TTS, STT and Image", () => {
    render(<Form />);
    openTypePicker();
    const offered = screen
      .getAllByRole("option")
      .map((o) => o.getAttribute("data-value"));
    expect(offered).toEqual(["llm", "tts", "stt", "image"]);
    for (const name of ["LLM", "TTS", "STT", "Image"])
      expect(
        screen.getByRole("option", { name: new RegExp(`^${name}`) }),
      ).toBeVisible();
  });

  it.each([
    ["TTS", "tts", "Media configuration"],
    ["STT", "stt", "Transcription configuration"],
    ["Image", "image", "Media configuration"],
  ])("picking %s selects it and opens its configuration", (label, value, shown) => {
    render(<Form />);
    openTypePicker();
    fireEvent.click(screen.getByRole("option", { name: new RegExp(`^${label}`) }));
    expect(screen.getByLabelText("selected experiment type")).toHaveTextContent(
      value,
    );
    expect(screen.getByText(shown)).toBeVisible();
    expect(screen.queryByText("Prompt configuration")).toBeNull();
  });

  it("keeps LLM as the default with its prompt configuration and Start from", () => {
    render(<Form />);
    expect(screen.getByLabelText("selected experiment type")).toHaveTextContent(
      "llm",
    );
    expect(screen.getByText("Prompt configuration")).toBeVisible();
    expect(screen.getByTestId("new-start-from")).toBeInTheDocument();
  });

  it("names each non-default type in the More settings summary", () => {
    expect(moreSettingsChanges({ experimentType: "llm" })).toEqual([]);
    expect(moreSettingsChanges({ experimentType: "tts" })).toEqual(["TTS"]);
    expect(moreSettingsChanges({ experimentType: "stt" })).toEqual(["STT"]);
    expect(moreSettingsChanges({ experimentType: "image" })).toEqual(["Image"]);
  });
});
