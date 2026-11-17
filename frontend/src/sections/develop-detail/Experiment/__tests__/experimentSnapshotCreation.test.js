import { afterEach, describe, expect, it } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";
import {
  emptyExperimentValues,
  evalFromDatasetDefault,
  valuesFromExperiment,
} from "../experimentFormValues";
import { getNewExperimentValidationSchema } from "../schema";
import { experimentBody, experimentBodyOfValues } from "../experimentBody";
import { hydrateHttpSourceValues, serverExperimentBody } from "../serverBody";
import { getExperimentDefaultValue } from "../utils";
import copiedMapping from "./copiedEvaluatorMapping.json";

const DATASET = "10000000-0000-4000-8000-000000000001";
const VERSION = "10000000-0000-4000-8000-000000000002";
const OTHER_VERSION = "10000000-0000-4000-8000-000000000003";
const SOURCE_COLUMN = "10000000-0000-4000-8000-000000000004";
const SNAPSHOT_COLUMN = "10000000-0000-4000-8000-000000000005";
const TEMPLATE = "10000000-0000-4000-8000-000000000006";
const PIN = "10000000-0000-4000-8000-000000000007";
const http = {
  url: "https://fixture.example/run",
  method: "POST",
  body_template: "{}",
  output_path: "$.reply",
};
const launch = () =>
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: true,
    madeReal: madeRealSurfaceIds(),
  });
afterEach(() => setLaunchOverrideForTest(null));

const detail = () => ({
  name: "Saved experiment",
  dataset_id: DATASET,
  column_id: SNAPSHOT_COLUMN,
  source_column_id: SOURCE_COLUMN,
  snapshot_dataset_id: "10000000-0000-4000-8000-000000000008",
  prompt_configs: [],
  user_eval_metrics: [],
  run_scope: {
    row_ids: ["original-row"],
    split_ids: ["original-split"],
    sample: { n: 1, seed: 42 },
  },
  runs: [
    { run_id: "a", dataset_version_id: VERSION },
    { run_id: "b", dataset_version: { id: VERSION } },
  ].map((run, index) => ({
    ...run,
    target_type: "prompt",
    target: {
      prompt_id: "10000000-0000-4000-8000-000000000012",
      prompt_version_id: "10000000-0000-4000-8000-000000000013",
      prompt_name: "Saved prompt",
      model: index === 0 ? "gpt-4o-mini" : "gpt-4o",
    },
  })),
});
const adapters = {
  native: (source, isEditing = false) =>
    valuesFromExperiment(source, { mode: isEditing ? "edit" : "duplicate" }),
  http: (source, isEditing = false) =>
    hydrateHttpSourceValues(
      { ...emptyExperimentValues(), ...getExperimentDefaultValue(source) },
      {
        ...source,
        runs: source.runs.map((run) => ({
          ...run,
          target_type: "http",
          target: {},
          http,
        })),
        http_configs: [{ id: "http-source", order: 0, http }],
      },
      { isEditing },
    ),
};

for (const [name, adapt] of Object.entries(adapters)) {
  describe(`${name} snapshot duplication`, () => {
    it("retains the canonical run version and runs its captured population whole", () => {
      launch();
      const source = detail();
      const before = structuredClone(source);
      const values = adapt(source);
      expect(values.datasetVersionId).toBe(VERSION);
      expect(values.splitIds).toEqual([]);
      expect(values.rowIds).toEqual([]);
      expect(values.sample).toBeNull();
      const wire = serverExperimentBody(
        experimentBodyOfValues(values, { datasetId: DATASET }),
      );
      expect(wire.blocks).toEqual([]);
      expect(wire.body.dataset_version_id).toBe(VERSION);
      expect(wire.body.row_ids).toBeUndefined();
      expect(wire.body.split_ids).toBeUndefined();
      expect(wire.body.sample).toBeUndefined();
      expect(source).toEqual(before);
    });

    it("rebinds the duplicate reference to its source while edits keep the snapshot", () => {
      launch();
      const source = detail();
      const values = adapt(source);
      expect(values.columnId).toBe(SOURCE_COLUMN);
      expect(adapt(source, true).columnId).toBe(SNAPSHOT_COLUMN);
      const wire = serverExperimentBody(
        experimentBodyOfValues(values, { datasetId: DATASET }),
      );
      expect(wire.body.column_id).toBe(SOURCE_COLUMN);
    });

    it("rebinds every copied evaluator input through validation and serialization", () => {
      launch();
      const source = {
        ...detail(),
        source_column_ids: copiedMapping.source_column_ids,
        user_eval_metrics: [
          {
            id: "10000000-0000-4000-8000-000000000011",
            template_id: TEMPLATE,
            name: "Saved judge",
            model: "turing_flash",
            config: {
              mapping: copiedMapping.snapshot_mapping,
              run_config: { pass_threshold: 0.75 },
            },
          },
        ],
      };
      const before = structuredClone(source);
      const values = adapt(source);
      const parsed = getNewExperimentValidationSchema([]).parse(values);
      const wire = serverExperimentBody(
        experimentBody(parsed, values, { datasetId: DATASET }),
      );
      expect(wire.blocks).toEqual([]);
      expect(wire.body.column_id).toBe(SOURCE_COLUMN);
      expect(wire.body.user_eval_metrics[0].config).toEqual({
        mapping: copiedMapping.source_mapping,
        run_config: { pass_threshold: 0.75 },
      });
      expect(adapt(source, true).userEvalMetrics[0].config.mapping).toEqual(
        copiedMapping.snapshot_mapping,
      );
      expect(source).toEqual(before);
    });

    it.each([".answer", "[0]", "::$.answer"])(
      "preserves a copied mapping path suffix %s",
      (suffix) => {
        const source = {
          ...detail(),
          source_column_ids: copiedMapping.source_column_ids,
          user_eval_metrics: [
            {
              template_id: TEMPLATE,
              name: "Saved judge",
              config: { mapping: { expected: SNAPSHOT_COLUMN + suffix } },
            },
          ],
        };
        expect(adapt(source).userEvalMetrics[0].config.mapping).toEqual({
          expected: SOURCE_COLUMN + suffix,
        });
        expect(adapt(source, true).userEvalMetrics[0].config.mapping).toEqual({
          expected: SNAPSHOT_COLUMN + suffix,
        });
      },
    );

    it("requires an explicit version choice when saved run pins disagree", () => {
      launch();
      const source = detail();
      source.runs[1].dataset_version.id = OTHER_VERSION;
      const values = adapt(source);
      expect(values.datasetVersionId).toBeNull();
      expect(values.datasetVersionConflict).toBe(true);
      const wire = serverExperimentBody(
        experimentBodyOfValues(values, { datasetId: DATASET }),
        { datasetVersionConflict: values.datasetVersionConflict },
      );
      expect(wire.blocks.map((block) => block.text)).toEqual([
        "The source experiment has no single dataset version. Choose which rows the duplicate should use on the Data step.",
      ]);
    });

    it("requires a reference choice when the source column is unavailable", () => {
      launch();
      const values = adapt({ ...detail(), source_column_id: null });
      expect(values.columnId).toBe(SNAPSHOT_COLUMN);
      expect(values.referenceColumnUnavailable).toBe(true);
      const wire = serverExperimentBody(
        experimentBodyOfValues(values, { datasetId: DATASET }),
        { referenceColumnUnavailable: values.referenceColumnUnavailable },
      );
      expect(wire.blocks.map((block) => block.text)).toEqual([
        "The source reference column is unavailable. Choose an expected answers column on the Data step.",
      ]);
    });

    it.each([
      { runs_has_more: true },
      { runs_next: 2 },
      { runs_total: 3 },
      { runs_availability: { available: false } },
      {
        runs: [
          { run_id: "a", dataset_version_id: VERSION },
          { run_id: "b", dataset_version_id: null },
        ],
      },
    ])(
      "does not infer a complete version choice from partial evidence %j",
      (partial) => {
        const values = adapt({ ...detail(), ...partial });
        expect(values.datasetVersionId).toBeNull();
        expect(values.datasetVersionConflict).toBe(true);
      },
    );

    it("retains an explicit top-level version for legacy readback", () => {
      const source = {
        ...detail(),
        runs: [],
        dataset_version: { id: VERSION, number: 3 },
      };
      expect(adapt(source).datasetVersionId).toBe(VERSION);
      expect(adapt(source).datasetVersionConflict).toBe(false);
    });
  });
}

describe("dataset default evaluator serialization", () => {
  it.each([PIN, null, undefined])(
    "preserves only an explicit evaluator pin: %s",
    (pin) => {
      launch();
      const binding = {
        id: "dataset-binding",
        template_id: TEMPLATE,
        name: "Correctness",
        version: pin ? "v1" : "v2",
        pinned_version_id: pin,
        config: { mapping: { input: SOURCE_COLUMN, output: "output" } },
      };
      const before = structuredClone(binding);
      const values = {
        ...emptyExperimentValues(),
        name: "New experiment",
        promptConfig: [
          {
            experimentType: "llm",
            targetType: "http",
            http: { ...http, bodyTemplate: "{}", outputPath: "$.reply" },
          },
        ],
        userEvalMetrics: [evalFromDatasetDefault(binding)],
      };
      const parsed = getNewExperimentValidationSchema([]).parse(values);
      const wire = serverExperimentBody(
        experimentBody(parsed, values, { datasetId: DATASET }),
      );
      expect(wire.blocks).toEqual([]);
      expect(wire.body.user_eval_metrics[0].config).toEqual({
        mapping: { input: SOURCE_COLUMN, output: "output" },
        ...(pin ? { version_pin: PIN } : {}),
      });
      expect(binding).toEqual(before);
    },
  );
});
