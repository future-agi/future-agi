/* eslint-disable react/prop-types */
// Restore row 5 (launch 1111): New experiment > TTS model card > voice
// picker > "+ Add Custom Voice". Production (origin/main 24cc6576) shows
// the link when the model's provider takes custom voices
// (voiceOptions.isCustomAudio <- custom_voice_supported) and it opens the
// custom voice dialog; the launch base removed it (692582580).
import React from "react";
import PropTypes from "prop-types";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useForm } from "react-hook-form";
import NewModelRenderWithParamsTool from "../StepperComponentExperiment/NewModelRenderWithParamsTool";
import VoiceSelectorPopover from "../VoiceSelectorPopover";

const query = vi.hoisted(() => ({ data: undefined, isSuccess: false }));
const voices = vi.hoisted(() => ({
  data: {
    voices: [
      { label: "Rachel", value: "rachel" },
      { label: "Adam", value: "adam" },
    ],
    isCustomAudio: true,
  },
  isLoading: false,
}));

vi.mock("src/api/develop/prompt", () => ({
  usePromptVersions: () => ({}),
}));
vi.mock("src/api/experiment/use-get-agents.js", () => ({
  useGetAgentVersions: () => ({}),
}));
vi.mock("../ExperimentFormContext", () => ({
  useExperimentForm: () => ({ mappingColumns: [] }),
}));
vi.mock(
  "src/components/custom-model-dropdown/CustomModelDropdownControl",
  () => ({ default: () => null }),
);
vi.mock("../ToolModesBlock", () => ({ default: () => null }));
vi.mock("../TryOnOneRow", () => ({ default: () => null }));
vi.mock("src/sections/common/RunSettings/RowLimits", () => ({
  default: () => null,
}));
vi.mock("@tanstack/react-query", () => ({ useQuery: () => query }));
vi.mock("src/api/develop/develop-detail", () => ({
  useVoiceOptions: () => voices,
}));
vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(() => {
      throw new Error("no network in this test");
    }),
  },
  endpoints: { develop: { modelParams: "/unused" } },
}));
vi.mock("src/components/image", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/custom-model-dropdown/CustomModelDropdown", () => ({
  default: () => null,
}));
vi.mock("../ModelParamsPopover", () => ({ default: () => null }));
// The real dialog posts to /model-hub/tts-voices/ through useMutation; the
// stub shows when it is open and lets the test report a created voice.
vi.mock("../../CustomAudioDialog", () => ({
  default: ({ open, selectedModel, onSuccess }) =>
    open ? (
      <div data-testid="custom-voice-dialog">
        {`${selectedModel?.value}|${selectedModel?.providers}`}
        <button
          type="button"
          onClick={() => onSuccess?.({ data: { result: { id: "cv-1" } } })}
        >
          Create voice
        </button>
      </div>
    ) : null,
}));

const MODEL = "eleven_multilingual_v2";
const VOICE_FIELD = "promptConfig.0.model.0.voices";
let readValues = null;

function Card({ providers = "elevenlabs" }) {
  const { control, setValue, getValues, watch } = useForm({
    defaultValues: {
      promptConfig: [
        {
          experimentType: "tts",
          model: [
            {
              value: MODEL,
              providers,
              voices: [{ voice: "rachel", promptConfigId: null }],
            },
          ],
          modelParams: {},
        },
      ],
    },
  });
  readValues = getValues;
  const selected = watch("promptConfig.0.model.0");
  return (
    <NewModelRenderWithParamsTool
      selectedModel={selected}
      index={0}
      control={control}
      setValue={setValue}
      getValues={getValues}
      voiceFieldName={VOICE_FIELD}
      modelParamsFieldName={`promptConfig.0.modelParams.${MODEL}`}
      modelType="tts"
    />
  );
}
Card.propTypes = { providers: PropTypes.string };

const openVoicePicker = () => {
  const label = screen.getByText("Selected voice");
  const button = label.nextElementSibling?.querySelector("button");
  expect(button).toBeTruthy();
  fireEvent.click(button);
  expect(screen.getByText("Select voice")).toBeTruthy();
};

beforeEach(() => {
  voices.data.isCustomAudio = true;
});
afterEach(() => {
  cleanup();
  readValues = null;
});

describe("restore row 5: + Add Custom Voice in the experiment voice picker", () => {
  it("the picker shows + Add Custom Voice when the provider takes custom voices, and it calls openModal", () => {
    const openModal = vi.fn();
    render(
      <VoiceSelectorPopover
        anchorEl={document.body}
        setAnchorEl={() => {}}
        fieldName={VOICE_FIELD}
        options={voices.data.voices}
        setValue={() => {}}
        currentValue={["rachel"]}
        isCustomAudio
        openModal={openModal}
      />,
    );
    const link = screen.getByRole("menuitem", { name: /Add Custom Voice/ });
    fireEvent.click(link);
    expect(openModal).toHaveBeenCalledTimes(1);
  });

  it("the picker has no + Add Custom Voice when the provider does not take custom voices", () => {
    render(
      <VoiceSelectorPopover
        anchorEl={document.body}
        setAnchorEl={() => {}}
        fieldName={VOICE_FIELD}
        options={voices.data.voices}
        setValue={() => {}}
        currentValue={["rachel"]}
        isCustomAudio={false}
        openModal={() => {}}
      />,
    );
    expect(screen.getByText("Select voice")).toBeTruthy();
    expect(screen.queryByText(/Add Custom Voice/)).toBeNull();
  });

  it("on the TTS model card the link opens the custom voice dialog and a created voice is added", () => {
    render(<Card providers="elevenlabs" />);
    expect(screen.queryByTestId("custom-voice-dialog")).toBeNull();

    openVoicePicker();
    fireEvent.click(
      screen.getByRole("menuitem", { name: /Add Custom Voice/ }),
    );

    const dialog = screen.getByTestId("custom-voice-dialog");
    expect(dialog.textContent).toContain(`${MODEL}|elevenlabs`);

    fireEvent.click(screen.getByRole("button", { name: "Create voice" }));
    expect(readValues(VOICE_FIELD)).toEqual([
      { voice: "rachel", promptConfigId: null },
      { voice: "cv-1", promptConfigId: null },
    ]);
  });

  it("on the TTS model card there is no link for a provider without custom voices", () => {
    voices.data.isCustomAudio = false;
    render(<Card providers="openai" />);
    openVoicePicker();
    expect(screen.queryByText(/Add Custom Voice/)).toBeNull();
  });
});
