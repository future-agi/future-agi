/* eslint-disable react/prop-types */
// Restore row 1 (launch 1111): New or Edit experiment > Configuration (TTS
// or Image type) > Add Prompt for a second prompt. Production (origin/main
// 24cc6576) always shows Add Prompt; the launch base hid it once one prompt
// existed (e245ce77d). With one prompt configured the button must still
// show, and clicking it must add a second prompt card.
import React from "react";
import PropTypes from "prop-types";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useForm } from "react-hook-form";
import ConfigureStepTTS from "../ConfigureStepTTS";
import { mediaRunsOf } from "../mediaRuns";

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../../common", () => ({
  getDefaultPromptConfigByModelType: (experimentType) => ({
    experimentType,
    model: [],
    messages: [{ role: "user", content: "" }],
  }),
}));
vi.mock("../ConfigureStepTTSItem", () => ({
  default: ({ index }) => (
    <div data-testid="tts-prompt-card">{`prompt ${index + 1}`}</div>
  ),
}));

let latestValues = null;

function Harness({ experimentType = "tts", prompts = 1 }) {
  const { control, setValue, clearErrors, watch, getValues, unregister } =
    useForm({
      defaultValues: {
        promptConfig: Array.from({ length: prompts }, (_, i) => ({
          experimentType,
          name: `Prompt ${i + 1}`,
          model: [{ value: "tts-1-hd", voices: [{ voice: "nova" }] }],
          messages: [{ role: "user", content: "Say {{text}}" }],
        })),
      },
    });
  latestValues = getValues;
  return (
    <ConfigureStepTTS
      control={control}
      allColumns={[]}
      setValue={setValue}
      clearErrors={clearErrors}
      errors={{}}
      watch={watch}
      jsonSchemas={{}}
      getValues={getValues}
      unregister={unregister}
      derivedVariables={{}}
    />
  );
}
Harness.propTypes = {
  experimentType: PropTypes.string,
  prompts: PropTypes.number,
};

afterEach(() => {
  cleanup();
  latestValues = null;
});

describe("restore row 1: TTS / Image experiment can add a second prompt", () => {
  it("shows Add Prompt while one TTS prompt is configured and adds a second", () => {
    render(<Harness experimentType="tts" prompts={1} />);
    expect(screen.getAllByTestId("tts-prompt-card")).toHaveLength(1);

    const add = screen.getByRole("button", { name: "Add Prompt" });
    fireEvent.click(add);

    expect(screen.getAllByTestId("tts-prompt-card")).toHaveLength(2);
    const config = latestValues("promptConfig");
    expect(config).toHaveLength(2);
    expect(config[1].experimentType).toBe("tts");
    // Still offered after the second prompt, as production.
    expect(screen.getByRole("button", { name: "Add Prompt" })).toBeTruthy();
  });

  it("shows Add Prompt while one Image prompt is configured", () => {
    render(<Harness experimentType="image" prompts={1} />);
    fireEvent.click(screen.getByRole("button", { name: "Add Prompt" }));
    expect(screen.getAllByTestId("tts-prompt-card")).toHaveLength(2);
    expect(latestValues("promptConfig")[1].experimentType).toBe("image");
  });

  it("still shows Add Prompt when the experiment has no prompt", () => {
    render(<Harness experimentType="tts" prompts={0} />);
    expect(screen.queryAllByTestId("tts-prompt-card")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Add Prompt" })).toBeTruthy();
  });

  it("the run list counts the runs of every prompt", () => {
    const twoPrompts = [
      {
        experimentType: "tts",
        model: [{ value: "tts-1-hd", voices: [{ voice: "nova" }] }],
      },
      {
        experimentType: "tts",
        model: [{ value: "tts-1-hd", voices: [{ voice: "alloy" }] }],
      },
    ];
    const runs = mediaRunsOf("tts", twoPrompts);
    expect(runs.map((r) => r.name)).toEqual([
      "TTS × tts-1-hd · nova",
      "TTS × tts-1-hd · alloy",
    ]);
    expect(runs.map((r) => r.letter)).toEqual(["A", "B"]);
  });
});
