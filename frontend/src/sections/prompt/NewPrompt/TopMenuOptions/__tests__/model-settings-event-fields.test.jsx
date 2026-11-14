import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

const MARK = "SYNTHETIC_TOOL_SETTING_3e88";

const mocks = vi.hoisted(() => {
  vi.stubEnv("VITE_MIXPANEL_TOKEN", "synthetic-project-token");
  return {
    mixpanel: {
      init: vi.fn(),
      track: vi.fn(),
      get_config: vi.fn(),
      set_config: vi.fn(),
      stop_session_recording: vi.fn(),
    },
  };
});

vi.mock("mixpanel-browser", () => ({ default: mocks.mixpanel }));
vi.mock("src/sections/develop-detail/Common/ModelOptions", () => ({
  default: () => null,
}));
vi.mock(
  "src/sections/develop-detail/Common/CustomModelSelection/CustomModelSelection",
  () => ({ CustomModelSelection: () => null }),
);
vi.mock(
  "src/sections/develop-detail/Common/ConfigureKeys/ConfigureKeys",
  () => ({
    default: () => null,
  }),
);
vi.mock("../DrawerHeaderbar", () => ({ default: () => null }));

import ModelSettings from "../ModelSettings";
import { Events } from "src/utils/Mixpanel";

describe("model settings saved analytics event", () => {
  beforeEach(() => {
    mocks.mixpanel.track.mockClear();
  });

  it("sends the model options and tool ids, not the tool definitions", async () => {
    const handleModelSettingData = vi.fn();
    render(
      <ModelSettings
        onClose={vi.fn()}
        handleLabelsAdd={vi.fn()}
        handleModelSettingData={handleModelSettingData}
        initialConfig={{
          model: "synthetic-model",
          temperature: 0.2,
          topP: 0.9,
          maxTokens: 256,
          presencePenalty: 0.5,
          frequencyPenalty: 0.4,
          responseFormat: { type: "json_schema", schema: { title: MARK } },
          toolChoice: "auto",
          // Tool rows as the tool picker stores them: a row id of its own and
          // the selected option, whose value is the tool's id.
          tools: [
            {
              id: "row-generated-1",
              tool: {
                label: "lookup",
                value: "tool-1",
                tool: {
                  id: "tool-1",
                  name: "lookup",
                  description: `Synthetic tool ${MARK}`,
                  config: { endpoint: `https://api.example.test/${MARK}` },
                },
              },
            },
          ],
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(handleModelSettingData).toHaveBeenCalled());
    const [, properties] = mocks.mixpanel.track.mock.calls.find(
      ([name]) => name === Events.modelSettingsApplied,
    );
    expect(properties).toEqual({
      form_fields: {
        config: {
          model: "synthetic-model",
          temperature: 0.2,
          topP: 0.9,
          maxTokens: 256,
          presencePenalty: 0.5,
          frequencyPenalty: 0.4,
          toolChoice: "auto",
          tool_ids: ["tool-1"],
        },
      },
    });
    expect(JSON.stringify(mocks.mixpanel.track.mock.calls)).not.toContain(MARK);
    expect(
      handleModelSettingData.mock.calls[0][0].config.tools[0].tool.tool.config,
    ).toEqual({ endpoint: `https://api.example.test/${MARK}` });
  });
});
