import { describe, expect, it } from "vitest";
import {
  buildBindingSavePayload,
  reconcileBindingSave,
} from "./bindingSavePayload";
import { buildEvalTemplateConfig } from "./evalPickerConfigUtils";
import snapshotCases from "./__tests__/bindingSnapshot.fixture.json";

const initial = {
  name: "binding",
  template_id: "template",
  pinned_version_id: "v1",
  model: "fixture-model",
  error_localizer: false,
  config: {
    mapping: { output: "column-a" },
    config: { rule_prompt: "Historic {{output}}" },
    run_config: {
      check_internet: false,
      data_injection: { full_row: true },
      tools: { saved: true },
    },
  },
};

const sparse = {
  ...initial,
  config: { mapping: initial.config.mapping, config: {}, run_config: {} },
};

describe("persisted snapshot contract (shared with test_picker_stored_pin.py)", () => {
  it.each(snapshotCases.cases)(
    "$name",
    ({ field, value, storedRunConfig, requestConfig }) => {
      const serialize = (effective) =>
        buildBindingSavePayload({
          templateId: "template",
          name: "binding",
          model: "fixture-model",
          versionId: "v1",
          mapping: { output: "output-column" },
          // Match draftSelection's complete runtime controls, not a stripped
          // selection that handleAdd never submits. The UI asserts this same
          // fixture through its real handler in EvalPickerConfigFull.test.jsx.
          ...snapshotCases.pickerRuntime,
          pass_threshold: effective.pass_threshold,
          multi_choice: effective.multi_choice,
          config: {
            ...buildEvalTemplateConfig({
              baseConfig: snapshotCases.base,
              evalType: "llm",
              instructions: effective.rule_prompt,
              messages: effective.messages,
              fewShotExamples: effective.few_shot_examples,
              outputType: "percentage",
              passThreshold: effective.pass_threshold,
              templateFormat: effective.template_format,
              multiChoice: effective.multi_choice,
            }),
            version_pin: "v1",
          },
        });
      const baseline = serialize(snapshotCases.base);
      const stored = {
        ...baseline,
        config: {
          mapping: baseline.config.mapping,
          config: {},
          run_config: storedRunConfig,
        },
      };
      const result = reconcileBindingSave(
        stored,
        baseline,
        serialize({ ...snapshotCases.base, [field]: value }),
        false,
        snapshotCases.base,
      );
      expect(result.changed).toBe(true);
      expect(result.payload).toEqual({
        name: "binding",
        template_id: "template",
        model: "fixture-model",
        error_localizer: false,
        config: requestConfig,
      });
    },
  );
});

describe("post-Save version pin equivalence", () => {
  const runtime = {
    model: "fixture-model",
    check_internet: false,
    error_localizer_enabled: false,
    multi_choice: false,
    pass_threshold: 0.5,
  };
  const snapshot = {
    ...runtime,
    rule_prompt: "Historic {{output}}",
    messages: [{ role: "system", content: "Historic {{output}}" }],
    required_keys: ["output"],
  };
  const stored = {
    ...initial,
    config: {
      mapping: initial.config.mapping,
      config: snapshot,
      run_config: runtime,
      reason_column: true,
      retained_binding_option: { enabled: true },
    },
  };
  const serialize = (config, versionId) =>
    buildBindingSavePayload({
      templateId: "template",
      name: "binding",
      mapping: initial.config.mapping,
      ...runtime,
      config: { ...config, version_pin: versionId },
      versionId,
    });
  const baseline = serialize(snapshot, "v1");
  const edited = {
    ...snapshot,
    rule_prompt: "Saved {{output}}",
    messages: [{ role: "system", content: "Saved {{output}}" }],
  };
  const savedVersion = {
    draft: { ...serialize(edited, "v1"), pinned_version_id: "v2" },
    // These absent fields are present as undefined in the local Save version
    // snapshot, but neither the version request nor the binding sends them.
    config: { ...edited, code: undefined, language: undefined },
  };

  it("retains the stored pin when a fully populated revert would deduplicate", () => {
    const before = JSON.stringify(stored);
    expect(
      reconcileBindingSave(
        stored,
        baseline,
        serialize(snapshot, "v2"),
        false,
        edited,
        savedVersion,
        snapshot,
      ),
    ).toEqual({ payload: stored, changed: false });
    expect(JSON.stringify(stored)).toBe(before);
  });

  it("does not confuse an omitted pin with explicit clearing or selecting a version", () => {
    for (const pinned_version_id of [null, "v2"]) {
      const draft = { ...baseline, pinned_version_id };
      expect(
        reconcileBindingSave(
          stored,
          baseline,
          draft,
          false,
          snapshot,
          undefined,
          snapshot,
        ),
      ).toEqual({
        payload: { ...stored, pinned_version_id },
        changed: true,
      });
    }
  });

  it("keeps a real config write enabled even if the returned definition matches V1", () => {
    const draft = serialize(snapshot, "v2");
    draft.config.mapping = { output: "column-b" };
    const result = reconcileBindingSave(
      stored,
      baseline,
      draft,
      false,
      edited,
      savedVersion,
      snapshot,
    );
    expect(result).toEqual({
      payload: {
        name: stored.name,
        template_id: stored.template_id,
        model: stored.model,
        error_localizer: false,
        config: { ...stored.config, mapping: { output: "column-b" } },
      },
      changed: true,
    });
  });

  it("does not assume deduplication when the stored pin has a different snapshot", () => {
    const pinnedSnapshot = { ...snapshot, check_internet: true };
    const result = reconcileBindingSave(
      stored,
      baseline,
      serialize(snapshot, "v2"),
      false,
      edited,
      savedVersion,
      pinnedSnapshot,
    );
    expect(result).toEqual({
      payload: {
        name: stored.name,
        template_id: stored.template_id,
        model: stored.model,
        error_localizer: false,
        config: stored.config,
      },
      changed: true,
    });
  });
});

describe("sparse binding edits", () => {
  it("leaves absent overrides and the pin exact for no-op and mapping-only saves", () => {
    expect(reconcileBindingSave(sparse, initial, initial, false)).toEqual({
      payload: sparse,
      changed: false,
    });
    const draft = {
      ...initial,
      config: { ...initial.config, mapping: { output: "column-b" } },
    };
    expect(reconcileBindingSave(sparse, initial, draft, false)).toEqual({
      payload: {
        ...sparse,
        config: { ...sparse.config, mapping: { output: "column-b" } },
      },
      changed: true,
    });
  });

  it.each([undefined, {}, { opaque: "retained" }])(
    "retains unchanged inherited keys and opaque stored keys in a new object override (%j)",
    (storedInjection) => {
      const stored = {
        ...sparse,
        config: {
          ...sparse.config,
          run_config:
            storedInjection === undefined
              ? {}
              : { data_injection: storedInjection },
        },
      };
      const draft = {
        ...initial,
        config: {
          ...initial.config,
          run_config: {
            ...initial.config.run_config,
            data_injection: { full_row: true, trace_context: true },
            tools: { saved: true, added: true },
          },
        },
      };
      const { payload } = reconcileBindingSave(stored, initial, draft, true);
      expect(payload.config.run_config.data_injection).toEqual({
        ...storedInjection,
        full_row: true,
        trace_context: true,
      });
      expect(payload.config.run_config.tools).toEqual({
        saved: true,
        added: true,
      });
      expect(payload.pinned_version_id).toBe("v1");
    },
  );

  it("builds an implicit version from the effective selected snapshot rather than sparse JSON", () => {
    const base = {
      rule_prompt: "Historic {{output}}",
      data_injection: { full_row: true },
      summary: { type: "custom", custom: "Opaque historical setting" },
      historical_only: true,
    };
    const draft = {
      ...initial,
      config: {
        ...initial.config,
        run_config: { ...initial.config.run_config, check_internet: true },
      },
    };
    const { payload } = reconcileBindingSave(
      sparse,
      initial,
      draft,
      false,
      base,
    );
    expect(payload).not.toHaveProperty("pinned_version_id");
    expect(payload.config.config).toMatchObject({
      ...base,
      check_internet: true,
    });
  });
});
