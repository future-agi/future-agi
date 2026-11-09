import React from "react";
import PropTypes from "prop-types";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  cleanup,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useForm } from "react-hook-form";
import NewModelRenderWithParamsTool from "../StepperComponentExperiment/NewModelRenderWithParamsTool";
import { escapeModelKey, promptConfigTransform } from "../utils";
import AgentPromptRenderer from "../StepperComponentExperiment/AgentPromptRenderer";
import { cardsFromExperiment } from "../StartFromExperiment";

const query = vi.hoisted(() => ({ data: undefined, isSuccess: false }));
const versions = vi.hoisted(() => ({
  data: {
    pages: [
      {
        results: [
          {
            id: "version-a",
            template_version: "v1",
            is_default: true,
            is_draft: false,
            variable_names: {},
            prompt_config_snapshot: {
              messages: [{ role: "user", content: "hi" }],
              configuration: { temperature: 0, max_tokens: 64 },
            },
          },
          {
            id: "version-b",
            template_version: "v2",
            is_draft: false,
            variable_names: {},
            prompt_config_snapshot: {
              messages: [{ role: "user", content: "hi" }],
              configuration: { temperature: 0.2, max_tokens: 128 },
            },
          },
        ],
      },
    ],
  },
}));
vi.mock("src/api/develop/prompt", () => ({
  usePromptVersions: () => versions,
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
  useVoiceOptions: () => ({ data: null, isLoading: false }),
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
vi.mock("../../CustomAudioDialog", () => ({ default: () => null }));
vi.mock("../VoiceSelectorPopover", () => ({ default: () => null }));
vi.mock("../ModelParamsPopover", () => ({ default: () => null }));

const saved = {
  temperature: 0,
  max_tokens: 64,
  top_p: 0.25,
  presence_penalty: 0.5,
  frequency_penalty: 0.75,
  response_format: { type: "json_object" },
  reasoning: { dropdowns: { reasoningEffort: "low" } },
};
const model = "gpt-4.1";
const field = `promptConfig.0.modelParams.${escapeModelKey(model)}`;

function Form({
  version = saved,
  versionId,
  providers = "",
  submit,
  initial = {},
  fullCard = false,
  experimentDetail,
  inspect,
}) {
  const { control, setValue, getValues, watch, unregister } = useForm({
    defaultValues: experimentDetail
      ? cardsFromExperiment(experimentDetail, {}, { mode: "edit" })
      : {
          promptConfig: [
            {
              promptId: "prompt-a",
              promptVersion: "version-a",
              model: [{ value: model, providers }],
              modelParams: initial,
            },
          ],
        },
  });
  return (
    <>
      {fullCard ? (
        <AgentPromptRenderer
          prompt={{ promptId: "prompt-a", name: "Saved prompt" }}
          index={0}
          control={control}
          setValue={setValue}
          getValues={getValues}
          watch={watch}
          unregister={unregister}
          fieldPrefix="promptConfig.0"
          type="prompt"
        />
      ) : (
        <NewModelRenderWithParamsTool
          selectedModel={{ value: model, providers }}
          index={0}
          control={control}
          setValue={setValue}
          getValues={getValues}
          modelParamsFieldName={field}
          modelType="llm"
          fallbackParams={version}
          fallbackVersionId={versionId}
        />
      )}
      <button
        onClick={() => setValue("promptConfig.0.promptVersion", "version-b")}
      >
        Change version
      </button>
      <button onClick={() => setValue(`${field}.temperature`, 0.9)}>
        Change temperature
      </button>
      <button
        onClick={() => {
          inspect?.(structuredClone(getValues("promptConfig")));
          submit(
            promptConfigTransform(
              getValues("promptConfig"),
              "llm",
              undefined,
              Boolean(experimentDetail),
            ),
          );
        }}
      >
        Submit
      </button>
    </>
  );
}

Form.propTypes = {
  version: PropTypes.object,
  versionId: PropTypes.string,
  providers: PropTypes.string,
  submit: PropTypes.func.isRequired,
  initial: PropTypes.object,
  fullCard: PropTypes.bool,
  experimentDetail: PropTypes.object,
  inspect: PropTypes.func,
};

const existingExperiment = {
  name: "Saved experiment",
  runs: [
    {
      run_id: "run-a",
      target_type: "prompt",
      config: {},
      target: {
        prompt_id: "prompt-a",
        prompt_version_id: "version-a",
        prompt_version_name: "v1",
        prompt_name: "Saved prompt",
        model,
        model_params: { temperature: 0, max_tokens: 64 },
      },
    },
  ],
};

beforeEach(() => {
  query.data = undefined;
  query.isSuccess = false;
});
afterEach(cleanup);

describe("saved prompt version parameters", () => {
  it.each(["edit", "duplicate"])(
    "initializes dotted keys through cardsFromExperiment in %s mode",
    (mode) => {
      const [card] = cardsFromExperiment(
        existingExperiment,
        {},
        { mode },
      ).promptConfig;
      expect(Object.keys(card.modelParams)).toEqual([escapeModelKey(model)]);
      expect(card.modelParams[escapeModelKey(model)]).toEqual({
        temperature: 0,
        max_tokens: 64,
      });
      expect(card.model[0].runId).toBe(mode === "edit" ? "run-a" : undefined);
      expect(
        promptConfigTransform([card], "llm")[0].model_params,
      ).toMatchObject({
        temperature: 0,
        max_tokens: 64,
      });
    },
  );

  it.each([false, true])(
    "re-hydrates a real existing dotted-model run while preserving its identity and session edits (%s)",
    async (edit) => {
      const submit = vi.fn();
      const inspect = vi.fn();
      render(
        <Form
          fullCard
          experimentDetail={existingExperiment}
          submit={submit}
          inspect={inspect}
        />,
      );
      fireEvent.click(screen.getByText("Submit"));
      const baseline = inspect.mock.lastCall[0][0].model[0];
      expect(baseline.runId).toBe("run-a");
      expect(baseline.ranWith).toBeDefined();
      expect(submit.mock.lastCall[0][0]).toMatchObject({
        run_id: "run-a",
        prompt_version: "version-a",
        model_params: { temperature: 0, max_tokens: 64 },
      });
      if (edit) fireEvent.click(screen.getByText("Change temperature"));
      fireEvent.click(screen.getByText("Change version"));
      await waitFor(() => {
        fireEvent.click(screen.getByText("Submit"));
        expect(submit.mock.lastCall[0][0]).toMatchObject({
          run_id: "run-a",
          prompt_version: "version-b",
          model: "gpt-4.1",
          model_params: { temperature: edit ? 0.9 : 0.2, max_tokens: 128 },
        });
      });
      expect(inspect.mock.lastCall[0][0].model[0]).toEqual(baseline);
      expect(screen.getByTestId("model-params-summary")).toHaveTextContent(
        `temp ${edit ? 0.9 : 0.2} · max 128 tokens`,
      );
    },
  );

  it("uses the actual prompt card's escaped dotted-model field when selecting a saved version", async () => {
    const submit = vi.fn();
    render(<Form fullCard submit={submit} />);
    await waitFor(() =>
      expect(screen.getByTestId("model-params-summary")).toHaveTextContent(
        "temp 0 · max 64 tokens",
      ),
    );
    fireEvent.click(screen.getByText("Submit"));
    expect(submit.mock.lastCall[0][0]).toMatchObject({
      prompt_version: "version-a",
      model: "gpt-4.1",
      model_params: { temperature: 0, max_tokens: 64 },
    });
    expect(submit.mock.lastCall[0][0].model_params).not.toHaveProperty(
      "maxTokens",
    );
  });

  it("hydrates without provider metadata and submits the displayed dotted-model values", async () => {
    const submit = vi.fn();
    render(<Form submit={submit} />);
    await waitFor(() =>
      expect(screen.getByTestId("model-params-summary")).toHaveTextContent(
        "temp 0 · max 64 tokens",
      ),
    );
    fireEvent.click(screen.getByText("Submit"));
    expect(submit.mock.calls[0][0][0].model_params).toMatchObject({
      temperature: 0,
      max_tokens: 64,
      top_p: 0.25,
      presence_penalty: 0.5,
      frequency_penalty: 0.75,
      response_format: { type: "json_object" },
      reasoning: { dropdowns: { reasoningEffort: "low" } },
    });
    // Without a schema the summary is read-only, not an empty editor.
    expect(screen.getByTestId("model-params-summary")).toBeDisabled();
  });

  it("applies a version arriving after an empty model schema, without providers", async () => {
    const submit = vi.fn();
    const view = render(<Form version={null} submit={submit} />);
    view.rerender(<Form version={saved} submit={submit} />);
    await waitFor(() => {
      fireEvent.click(screen.getByText("Submit"));
      expect(submit.mock.lastCall[0][0].model_params).toMatchObject({
        temperature: 0,
        max_tokens: 64,
      });
    });
  });

  it("version values override schema defaults, and defaults fill only missing keys", async () => {
    query.data = {
      sliders: [
        { label: "temperature", default: 1 },
        { label: "max_tokens", default: 1024 },
        { label: "top_p", default: 0.8 },
      ],
    };
    query.isSuccess = true;
    const submit = vi.fn();
    render(
      <Form
        providers="openai"
        version={{ temperature: 0, max_tokens: 64 }}
        submit={submit}
      />,
    );
    await waitFor(() => {
      fireEvent.click(screen.getByText("Submit"));
      expect(submit.mock.lastCall[0][0].model_params).toMatchObject({
        temperature: 0,
        max_tokens: 64,
        top_p: 0.8,
      });
    });
  });

  it("changing the selected version updates inherited values but keeps a user edit", async () => {
    const submit = vi.fn();
    const view = render(<Form submit={submit} />);
    await waitFor(() =>
      expect(screen.getByTestId("model-params-summary")).toHaveTextContent(
        "max 64 tokens",
      ),
    );
    fireEvent.click(screen.getByText("Change temperature"));
    view.rerender(
      <Form version={{ temperature: 0.2, max_tokens: 128 }} submit={submit} />,
    );
    await waitFor(() => {
      fireEvent.click(screen.getByText("Submit"));
      expect(submit.mock.lastCall[0][0].model_params).toMatchObject({
        temperature: 0.9,
        max_tokens: 128,
      });
    });
  });

  it.each([false, true])(
    "switches an edited run to the new version, keeping only session edits (%s)",
    async (edit) => {
      const submit = vi.fn();
      const initial = {
        [escapeModelKey(model)]: { temperature: 0.7, max_tokens: 256 },
      };
      const view = render(<Form initial={initial} submit={submit} />);
      fireEvent.click(screen.getByText("Submit"));
      expect(submit.mock.lastCall[0][0].model_params).toMatchObject({
        temperature: 0.7,
        max_tokens: 256,
      });
      if (edit) fireEvent.click(screen.getByText("Change temperature"));
      view.rerender(
        <Form
          initial={initial}
          version={{ temperature: 0.2, max_tokens: 128 }}
          submit={submit}
        />,
      );
      await waitFor(() => {
        fireEvent.click(screen.getByText("Submit"));
        expect(submit.mock.lastCall[0][0].model_params).toMatchObject({
          temperature: edit ? 0.9 : 0.2,
          max_tokens: 128,
        });
      });
      expect(screen.getByTestId("model-params-summary")).toHaveTextContent(
        "max 128 tokens",
      );
    },
  );

  it("re-hydrates persisted dotted params through the actual prompt card on version change", async () => {
    const submit = vi.fn();
    render(
      <Form
        fullCard
        initial={{
          [escapeModelKey(model)]: { temperature: 0.7, max_tokens: 256 },
        }}
        submit={submit}
      />,
    );
    fireEvent.click(screen.getByText("Change temperature"));
    fireEvent.click(screen.getByText("Change version"));
    await waitFor(() => {
      fireEvent.click(screen.getByText("Submit"));
      expect(submit.mock.lastCall[0][0]).toMatchObject({
        prompt_version: "version-b",
        model_params: { temperature: 0.9, max_tokens: 128 },
      });
    });
  });

  it("keeps persisted values on late initial fetch, but re-seeds on a new version id", async () => {
    const submit = vi.fn();
    const initial = {
      [escapeModelKey(model)]: { temperature: 0.7, max_tokens: 256 },
    };
    const view = render(
      <Form initial={initial} version={null} versionId="a" submit={submit} />,
    );
    view.rerender(
      <Form initial={initial} version={saved} versionId="a" submit={submit} />,
    );
    fireEvent.click(screen.getByText("Submit"));
    expect(submit.mock.lastCall[0][0].model_params).toMatchObject({
      temperature: 0.7,
      max_tokens: 256,
    });
    view.rerender(
      <Form initial={initial} version={saved} versionId="b" submit={submit} />,
    );
    await waitFor(() => {
      fireEvent.click(screen.getByText("Submit"));
      expect(submit.mock.lastCall[0][0].model_params).toMatchObject({
        temperature: 0,
        max_tokens: 64,
      });
    });
  });

  it("does not replace a kept run's explicit parameters", async () => {
    const submit = vi.fn();
    const detail = structuredClone(existingExperiment);
    detail.runs[0].target.model_params = {
      temperature: 0.7,
      max_tokens: 256,
      response_format: { type: "json_object" },
    };
    render(<Form fullCard experimentDetail={detail} submit={submit} />);
    await waitFor(() =>
      expect(screen.getByTestId("model-params-summary")).toHaveTextContent(
        "temp 0.7 · max 256 tokens",
      ),
    );
    fireEvent.click(screen.getByText("Submit"));
    expect(submit.mock.lastCall[0][0].model_params).toMatchObject({
      temperature: 0.7,
      max_tokens: 256,
      response_format: { type: "json_object" },
    });
  });
});
