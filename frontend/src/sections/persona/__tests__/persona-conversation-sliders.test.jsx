import React from "react";
import PropTypes from "prop-types";
import { FormProvider, useForm } from "react-hook-form";
import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";

import PersonaConversationSetting from "src/sections/persona/PersonaCreateEdit/PersonaConversationSetting";

const Harness = ({ children }) => {
  const methods = useForm({
    defaultValues: {
      language: [],
      multilingual: false,
      conversationSpeed: [],
      backgroundSound: "false",
      finishedSpeakingSensitivity: 5,
      interruptSensitivity: 5,
    },
  });
  return <FormProvider {...methods}>{children}</FormProvider>;
};

Harness.propTypes = { children: PropTypes.node };

// Each 1-10, step-1 sensitivity slider must draw one dot per stop the handle
// can land on: values 1..10, which sit at 0%, 11.1%, ..., 100% of the rail.
const markOffsets = (slider) =>
  Array.from(
    slider.closest(".MuiSlider-root").querySelectorAll(".MuiSlider-mark"),
  ).map((mark) => parseFloat(mark.style.left));

const expectedOffsets = Array.from({ length: 10 }, (_, i) => (i / 9) * 100);

describe("Persona conversation sensitivity sliders", () => {
  it("place a tick mark on every value the handle can stop on", () => {
    render(
      <Harness>
        <PersonaConversationSetting multiple={false} />
      </Harness>,
    );

    const sliders = screen.getAllByRole("slider");
    expect(sliders).toHaveLength(2);

    for (const slider of sliders) {
      expect(slider).toHaveAttribute("min", "1");
      expect(slider).toHaveAttribute("max", "10");
      expect(slider).toHaveAttribute("step", "1");

      const offsets = markOffsets(slider);
      expect(offsets).toHaveLength(expectedOffsets.length);
      offsets.forEach((offset, i) => {
        expect(offset).toBeCloseTo(expectedOffsets[i], 6);
      });
    }
  });
});
