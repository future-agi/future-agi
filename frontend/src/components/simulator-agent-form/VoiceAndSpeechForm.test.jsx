import React from "react";
import { FormProvider, useForm } from "react-hook-form";
import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";

import VoiceAndSpeechForm from "./VoiceAndSpeechForm";

function Harness() {
  const methods = useForm({
    defaultValues: {
      voiceProvider: "",
      voiceName: "",
      interruptSensitivity: 5,
      conversationSpeed: 1,
      finishedSpeakingSensitivity: 5,
    },
  });

  return (
    <FormProvider {...methods}>
      <VoiceAndSpeechForm control={methods.control} errors={{}} />
    </FormProvider>
  );
}

describe("Voice and Speech slider marks", () => {
  it("places Conversation Speed marks only at its six selectable values", () => {
    render(<Harness />);

    const slider = screen.getAllByRole("slider")[1];
    expect(slider).toHaveAttribute("min", "0.7");
    expect(slider).toHaveAttribute("max", "1.2");
    expect(slider).toHaveAttribute("step", "0.1");

    const root = slider.closest(".MuiSlider-root");
    const offsets = Array.from(root.querySelectorAll(".MuiSlider-mark")).map(
      (mark) => parseFloat(mark.style.left),
    );
    expect(offsets).toHaveLength(6);
    offsets.forEach((offset, index) => {
      expect(offset).toBeCloseTo([0, 20, 40, 60, 80, 100][index], 6);
    });

    // Move the real input to a stop and verify its thumb sits on that mark.
    fireEvent.change(slider, { target: { value: "0.8" } });
    expect(slider).toHaveAttribute("aria-valuenow", "0.8");
    expect(
      parseFloat(root.querySelector(".MuiSlider-thumb").style.left),
    ).toBeCloseTo(20, 6);
  });

  it("preserves the step-aligned marks on both sensitivity sliders", () => {
    render(<Harness />);

    const sliders = screen.getAllByRole("slider");
    expect(
      sliders[0].closest(".MuiSlider-root").querySelectorAll(".MuiSlider-mark"),
    ).toHaveLength(21);
    expect(
      sliders[2].closest(".MuiSlider-root").querySelectorAll(".MuiSlider-mark"),
    ).toHaveLength(11);
  });
});
