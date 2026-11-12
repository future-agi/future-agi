import React from "react";
import PropTypes from "prop-types";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useForm } from "react-hook-form";
import AgentPromptRenderer from "../StepperComponentExperiment/AgentPromptRenderer";
import { escapeModelKey, promptConfigTransform } from "../utils";
import { cardsFromExperiment } from "../StartFromExperiment";
import { editChangeSummary, editRunScope } from "../experimentFormValues";
import { flattenRuns } from "../runPlan";

const query = vi.hoisted(() => ({ data: undefined, isSuccess: false }));
const versions = vi.hoisted(() => ({ data: undefined }));
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
  () => ({
    default: () => null,
  }),
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

const saved = { temperature: 0, max_tokens: 64 };
const snapshot = {
  messages: [{ role: "user", content: "hi" }],
  configuration: saved,
};
const setSnapshot = (value) => {
  versions.data = {
    pages: [
      {
        results: [
          {
            id: "version-a",
            template_version: "v1",
            is_default: true,
            is_draft: false,
            variable_names: {},
            prompt_config_snapshot: value,
          },
        ],
      },
    ],
  };
};
const detailFor = (model, params = saved) => ({
  name: "Saved experiment",
  runs: [
    {
      run_id: "run-a",
      label: "#1A",
      target_type: "prompt",
      config: {},
      target: {
        prompt_id: "prompt-a",
        prompt_version_id: "version-a",
        prompt_version_name: "v1",
        prompt_name: "Saved prompt",
        model,
        model_params: params,
      },
    },
  ],
});

let api;
function Form({ detail, fresh = false }) {
  const initial = React.useMemo(() => {
    const values = cardsFromExperiment(detail, {}, { mode: "edit" });
    if (fresh) {
      values.promptConfig[0].modelParams = {};
      delete values.promptConfig[0].model[0].runId;
      delete values.promptConfig[0].model[0].ranWith;
    }
    return values;
  }, [detail, fresh]);
  const form = useForm({ defaultValues: initial });
  api = { ...form, initial };
  return (
    <AgentPromptRenderer
      prompt={{ promptId: "prompt-a", name: "Saved prompt" }}
      index={0}
      control={form.control}
      setValue={form.setValue}
      getValues={form.getValues}
      watch={form.watch}
      unregister={form.unregister}
      fieldPrefix="promptConfig.0"
      type="prompt"
    />
  );
}
Form.propTypes = { detail: PropTypes.object.isRequired, fresh: PropTypes.bool };

const body = () =>
  promptConfigTransform(
    api.getValues("promptConfig"),
    "llm",
    undefined,
    true,
  )[0];
const options = (detail) => ({
  runs: detail.runs,
  flatten: flattenRuns,
  columns: [],
});
const summary = (detail) =>
  editChangeSummary(api.initial, api.getValues(), options(detail));
const scope = (detail) =>
  editRunScope(api.initial, api.getValues(), options(detail));
const hydrated = () =>
  waitFor(() =>
    expect(screen.getByTestId("model-params-summary")).toHaveTextContent(
      "temp 0 · max 64 tokens",
    ),
  );

beforeEach(() => setSnapshot(snapshot));
afterEach(() => {
  cleanup();
  api = null;
});

describe("saved-version edit confirm and estimate", () => {
  it.each(["gpt-4o-mini", "gpt-4.1"])(
    "keeps an untouched hydrated run (%s)",
    async (model) => {
      const detail = detailFor(model);
      render(<Form detail={detail} />);
      await hydrated();
      expect(body()).toMatchObject({ run_id: "run-a", model_params: saved });
      expect(summary(detail)).toMatchObject({
        kept: ["#1A"],
        rerun: [],
        changes: {},
        text: "Kept: #1A",
      });
      // The footer estimates no work, exactly as it did before hydration.
      expect(scope(detail)).toEqual(
        editRunScope(api.initial, api.initial, options(detail)),
      );
      expect(scope(detail).promptConfig).toEqual([]);
    },
  );

  it.each(["gpt-4o-mini", "gpt-4.1"])(
    "reports only the real parameter edit (%s)",
    async (model) => {
      const detail = detailFor(model);
      render(<Form detail={detail} />);
      await hydrated();
      act(() =>
        api.setValue(
          `promptConfig.0.modelParams.${escapeModelKey(model)}.temperature`,
          0.9,
        ),
      );
      await waitFor(() =>
        expect(body().model_params).toMatchObject({
          temperature: 0.9,
          max_tokens: 64,
        }),
      );
      expect(summary(detail)).toMatchObject({
        kept: [],
        rerun: ["#1A"],
        changes: { "#1A": ["params temperature 0 → 0.9"] },
        text: "Re-run: #1A (params temperature 0 → 0.9)",
      });
      expect(scope(detail)).toMatchObject({
        kept: [],
        rerun: ["#1A"],
        added: [],
      });
      expect(flattenRuns(scope(detail).promptConfig)).toHaveLength(1);
      expect(body().run_id).toBe("run-a");
    },
  );

  it("normalizes legacy camelCase ranWith as well as the editor params", async () => {
    const detail = detailFor("gpt-4o-mini", {
      temperature: 0,
      maxTokens: 64,
      topP: null,
    });
    render(<Form detail={detail} />);
    await hydrated();
    expect(summary(detail)).toMatchObject({
      kept: ["#1A"],
      rerun: [],
      changes: {},
    });
    expect(scope(detail).promptConfig).toEqual([]);
    expect(body().model_params).toMatchObject(saved);
  });

  it.each(["gpt-4o-mini", "gpt-4.1"])(
    "compares legacy cards without ranWith using canonical keys (%s)",
    (model) => {
      const detail = detailFor(model);
      const initial = cardsFromExperiment(detail, {}, { mode: "edit" });
      delete initial.promptConfig[0].model[0].ranWith;
      // Old form state can still carry an unescaped model key.
      initial.promptConfig[0].modelParams = {
        [model]: { max_tokens: 64, temperature: 0 },
      };
      const current = structuredClone(initial);
      current.promptConfig[0].modelParams = {
        [escapeModelKey(model)]: { temperature: 0, maxTokens: 64, topP: null },
      };
      expect(editChangeSummary(initial, current, options(detail)).text).toBe(
        "Kept: #1A",
      );
      expect(
        editRunScope(initial, current, options(detail)).promptConfig,
      ).toEqual([]);
      current.promptConfig[0].modelParams[escapeModelKey(model)].maxTokens =
        128;
      expect(editChangeSummary(initial, current, options(detail)).text).toBe(
        "Re-run: #1A (params max_tokens 64 → 128)",
      );
    },
  );
});

describe("saved snapshot configuration shapes", () => {
  it("hydrates and submits the first list snapshot, not a later entry", async () => {
    setSnapshot([
      snapshot,
      { configuration: { temperature: 1, max_tokens: 999 } },
    ]);
    render(<Form detail={detailFor("gpt-4.1")} fresh />);
    await hydrated();
    expect(body().model_params).toMatchObject(saved);
    expect(body().model_params).not.toHaveProperty("maxTokens");
  });

  it.each([
    ["empty list", []],
    ["non-object first entry", [null, snapshot]],
    ["nested list", [[snapshot]]],
    ["scalar snapshot", "invalid"],
    ["null snapshot", null],
    ["scalar configuration", { configuration: "invalid" }],
    ["list configuration", { configuration: [saved] }],
    ["null configuration", { configuration: null }],
  ])("treats %s as empty configuration", (_name, value) => {
    setSnapshot(value);
    render(<Form detail={detailFor("gpt-4.1")} fresh />);
    expect(body().model_params).toEqual({ providers: "", logoUrl: "" });
    expect(
      screen.queryByTestId("model-params-summary"),
    ).not.toBeInTheDocument();
  });
});
