// Reads and writes of the New / Edit experiment drawer (PRODUCT-SPEC §3.8).
// Versions (N1) and splits (N6) are uncontracted prototype endpoints; the
// rest are existing endpoints with additive fields.
import { useMutation, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { absentTemplateOf } from "src/launch/absentPathGuard";
import { SHOW_UNRELEASED_PARTS, isLaunchBuild } from "src/launch/launchSwitch";
import { tableRequestFor } from "src/sections/develop-detail/DataTab/Versions/versionedSource";
import { useCreateAdmission } from "src/sections/develop-detail/Experiment/useCreateAdmission";
import { VERSION_LIST_LIMIT } from "src/sections/develop-detail/Experiment/datasetVersionChoice";

const SAMPLE_PAGE_SIZE = 50;

const NO_VERSIONS = Object.freeze([]);

/** suggest-name -> { suggested_name, next_ordinal }. */
export function useExperimentNameSuggestion(
  datasetId,
  { enabled = true } = {},
) {
  return useQuery({
    queryKey: ["experiment-suggested-name", datasetId],
    queryFn: () =>
      axios.get(endpoints.develop.experiment.suggestName(datasetId)),
    select: (res) => {
      const r = res?.data?.result ?? {};
      return {
        suggestedName: r.suggested_name ?? r.suggestedName ?? "",
        nextOrdinal: r.next_ordinal ?? r.nextOrdinal ?? null,
      };
    },
    enabled: Boolean(datasetId) && enabled,
    staleTime: 0,
  });
}

/** validate-name -> true when the name is free on the dataset. */
export function useExperimentNameCheck(
  datasetId,
  name,
  { enabled = true } = {},
) {
  const trimmed = String(name ?? "").trim();
  return useQuery({
    queryKey: ["experiment-validate-name", datasetId, trimmed],
    queryFn: () =>
      axios.get(endpoints.develop.experiment.validateName, {
        params: { dataset_id: datasetId, name: trimmed },
      }),
    select: (res) => {
      const r = res?.data?.result ?? {};
      return r.is_valid ?? r.isValid ?? true;
    },
    enabled: Boolean(datasetId && trimmed) && enabled,
    staleTime: 10_000,
  });
}

/**
 * N1 · dataset versions, newest first, at most VERSION_LIST_LIMIT. `meta`
 * goes to the query (`errorHandled` when the caller shows the failure).
 */
export function useExperimentDatasetVersions(
  datasetId,
  { enabled = true, meta } = {},
) {
  // `held`: this build refuses the version list (absent-path guard), so the
  // picker says "Not available in this release" instead of waiting. The
  // guard is installed only when unreleased parts are hidden
  // (installAbsentPathGuard); with them shown the list is read.
  const held =
    Boolean(datasetId) &&
    isLaunchBuild() &&
    !SHOW_UNRELEASED_PARTS &&
    Boolean(absentTemplateOf(endpoints.develop.versions.list(datasetId)));
  const q = useQuery({
    // Its own cache entry, under the Data tab's key (dataset-versions.js):
    // the Data tab never reads the wizard's list, and refreshing the Data
    // tab's key after a version save refreshes this one too.
    queryKey: ["develop", "dataset-versions", datasetId, "experiment-wizard"],
    queryFn: () =>
      axios.get(endpoints.develop.versions.list(datasetId), {
        params: { limit: VERSION_LIST_LIMIT },
      }),
    meta,
    select: (res) => {
      const versions = res?.data?.result?.versions;
      if (res?.data?.status !== true || !Array.isArray(versions)) {
        throw new Error("Dataset version history could not be loaded.");
      }
      return versions;
    },
    // Optional history must not block setup against the dataset's current rows.
    enabled: Boolean(datasetId) && enabled && !held,
  });
  return { ...q, held };
}

/**
 * The version lists the wizard reads. A launch build runs the dataset's
 * current rows: the server copies them when the experiment is created and
 * keeps that copy as the experiment's own version. A version saved earlier
 * can't be run yet, so its saved versions are only listed on the Data step
 * (`savedVersions`) and no other step reads one as the version that runs.
 */
export function wizardVersionsOf(list, launch = isLaunchBuild()) {
  const all = Array.isArray(list) ? list : NO_VERSIONS;
  return launch
    ? { versions: NO_VERSIONS, savedVersions: all, runsCurrentRows: true }
    : { versions: all, savedVersions: NO_VERSIONS, runsCurrentRows: false };
}

/** N6 · splits of the dataset with their row counts. */
export function useExperimentDatasetSplits(datasetId, { enabled = true } = {}) {
  return useQuery({
    queryKey: ["develop", "dataset-splits", datasetId],
    queryFn: () => axios.get(endpoints.develop.splits.list(datasetId)),
    select: (res) => ({
      splits: res?.data?.result?.splits ?? [],
      noSplitCount: res?.data?.result?.no_split_count ?? 0,
    }),
    enabled: Boolean(datasetId) && enabled,
  });
}

/** The `system:splits` grid filter (CONTRACT rule 5). */
export const splitsFilter = (splitIds = []) =>
  splitIds.length
    ? [
        {
          column_id: "system:splits",
          filter_config: {
            filter_type: "categorical",
            filter_op: "in",
            filter_value: splitIds,
          },
        },
      ]
    : [];

/**
 * First rows of the chosen version and splits: raw column_config (with
 * `role`), rows for the sample picker and the example count.
 */
export function useExperimentSampleTable(
  { datasetId, versionId, versionNumber, isLatest, splitIds = [] },
  { enabled = true } = {},
) {
  const { url, params } = tableRequestFor({
    datasetId,
    versionNumber: isLatest ? null : versionNumber,
    versionId: isLatest ? null : versionId,
    params: {
      filters: JSON.stringify(splitsFilter(splitIds)),
      page_size: SAMPLE_PAGE_SIZE,
      current_page_index: 0,
    },
  });
  return useQuery({
    queryKey: [
      "experiment-create",
      "sample-table",
      datasetId,
      isLatest ? "latest" : versionId,
      [...splitIds].sort().join(","),
    ],
    queryFn: () => axios.get(url, { params }),
    select: (res) => {
      const r = res?.data?.result ?? {};
      return {
        columns: r.column_config ?? [],
        rows: r.table ?? [],
        total: r.metadata?.total_rows ?? null,
        datasetName: r.metadata?.dataset_name ?? null,
      };
    },
    enabled: Boolean(datasetId) && enabled,
    placeholderData: (prev) => prev,
  });
}

/** get_evals_list (user evals on the dataset, with the additive fields). */
export function useDatasetEvalsForExperiment(
  datasetId,
  { enabled = true } = {},
) {
  return useQuery({
    queryKey: ["experiment-create", "dataset-evals", datasetId],
    queryFn: () =>
      axios.get(endpoints.develop.eval.getEvalsList(datasetId), {
        params: { eval_type: "user" },
      }),
    select: (res) => res?.data?.result?.evals ?? [],
    enabled: Boolean(datasetId) && enabled,
    staleTime: 0,
  });
}

/**
 * Create response: the object of PRODUCT-SPEC §6.1, or the legacy string
 * "Experiment created successfully. experiment_id=<uuid>".
 */
export function parseCreateResponse(res) {
  const r = res?.data?.result;
  if (r && typeof r === "object")
    return {
      experimentId: r.experiment_id ?? null,
      ordinal: r.experiment_ordinal ?? null,
      examples: r.examples ?? null,
      runs: r.runs ?? [],
    };
  const m = /experiment_id=([0-9a-f-]{36})/i.exec(String(r ?? ""));
  return {
    experimentId: m?.[1] ?? null,
    ordinal: null,
    examples: null,
    runs: [],
  };
}

/** POST experiments/v2/. */
export const useCreateExperiment = (options = {}) =>
  useCreateAdmission(
    (body) => axios.post(endpoints.develop.experiment.create(), body),
    options,
  );

/** PUT experiments/v2/{id}/ (one call; the backend re-runs what changed). */
export const useUpdateExperiment = (experimentId, options = {}) =>
  useMutation({
    mutationFn: (body) =>
      axios.put(endpoints.develop.experiment.update(experimentId), body),
    ...options,
  });

/**
 * `Test on Row N`: POST eval-playground with the row as context.
 * vars: { templateId, model, mapping, datasetId, rowId, output }
 */
export const useTestEvalOnRow = (options = {}) =>
  useMutation({
    mutationFn: ({
      templateId,
      model,
      mapping,
      columnMapping,
      threshold,
      outputType,
      datasetId,
      rowId,
      output,
      decision,
      instructions,
    }) =>
      axios.post(endpoints.develop.eval.evalPlayground, {
        template_id: templateId,
        model: model || "turing_large",
        error_localizer: false,
        // The card's edited pass bar and output type: Test scores like the
        // run will (same scorer, same threshold, same judge).
        config: {
          mapping: mapping ?? {},
          ...(threshold != null ? { threshold } : {}),
          ...(outputType ? { output_type: outputType } : {}),
          // W1: a decision-model judge's question (model, format, labels)
          // and its instructions: the eval's prompt as the editor saved it.
          ...(decision ? { decision } : {}),
          ...(decision && instructions ? { rule_prompt: instructions } : {}),
        },
        row_context: {
          source: "experiment_create",
          dataset_id: datasetId,
          row_id: rowId,
          output,
          ...(columnMapping ? { column_mapping: columnMapping } : {}),
        },
      }),
    ...options,
  });
