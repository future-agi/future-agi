import { getNumberValidation } from "src/utils/validation";
import { z } from "zod";
import {
  presetToRange,
  presetToToken,
} from "src/sections/projects/timeWindowPresets";
import { serializeTaskFilterRowsForApi } from "src/sections/common/EvalsTasks/task_filter_serialization";

const TASK_FILTER_PROPERTY_TO_API = {
  span_kind: "observation_type",
  observation_type: "observation_type",
};

export const getTaskFilterApiKey = (property) =>
  TASK_FILTER_PROPERTY_TO_API[property] || property;

// Form-row `property` → outer-filters sibling key the BE honors. `node_type`
// is a FE alias for `observation_type` (the eval-task handler can't resolve
// it directly), so route it via the dedicated sibling branch.
const TOP_LEVEL_SIBLING_KEY_BY_PROPERTY = {
  observation_type: "observation_type",
  node_type: "observation_type",
  span_kind: "observation_type",
  session_id: "session_id",
  trace_id: "trace_id",
};

// One form row → one wire entry. Cross-row composition is the BE's job —
// merging same-column rows would collapse "not_contains A AND not_contains B"
// into "in [A, B]" and invert intent. OR is expressed within a single multi-
// value `in`/`not_in` row, not across rows.
export const extractAttributeFilters = (filters) => {
  const attributeRows = (filters || []).filter((f) => {
    if (!f) return false;
    // Sibling keys are emitted separately by getNewTaskFilters.
    if (f.property in TOP_LEVEL_SIBLING_KEY_BY_PROPERTY) return false;
    // Legacy rows with neither apiColType nor propertyId are BE no-ops.
    if (!f.propertyId && f.property !== "attributes") return false;
    return true;
  });

  return serializeTaskFilterRowsForApi(attributeRows);
};

// Sibling-key extraction: rows whose property maps to a top-level BE key
// (observation_type / node_type / session_id) → flat per-field array.
const extractSiblingFilters = (filters) => {
  const out = {};
  (filters || []).forEach((f) => {
    const beKey = TOP_LEVEL_SIBLING_KEY_BY_PROPERTY[f?.property];
    if (!beKey) return;
    const val = f?.filterConfig?.filterValue;
    const vals = Array.isArray(val)
      ? val
      : val !== undefined && val !== null && val !== ""
        ? [val]
        : [];
    if (vals.length === 0) return;
    if (out[beKey]) {
      out[beKey].push(...vals);
    } else {
      out[beKey] = [...vals];
    }
  });
  return out;
};

export const getNewTaskFilters = (data, projectId, ignoreDate = false) => {
  const filters = { project_id: projectId?.length ? projectId : null };

  const attributeFilters = extractAttributeFilters(data?.filters);
  Object.assign(filters, extractSiblingFilters(data?.filters));

  if (data?.runType === "historical" && !ignoreDate) {
    // A relative preset re-anchors to now on save; Custom is kept verbatim.
    // Writing the key every save is also what migrates pre-existing tasks.
    const preset = data?.datePreset || "Custom";
    const range = presetToRange(preset);
    const [start, end] = range || [data?.startDate, data?.endDate];
    filters["date_preset"] = presetToToken(preset);
    filters["date_range"] = [
      new Date(start).toISOString(),
      new Date(end).toISOString(),
    ];
  }

  return { filters, attributeFilters };
};

export const NewTaskValidationSchema = () =>
  z
    .object({
      name: z.string().min(1, { message: "Name is required" }),
      project: z.string().min(1, { message: "Project is required" }),
      spansLimit: z.union([
        z.string().optional(),
        getNumberValidation("Max Spans is required"),
      ]),
      samplingRate: getNumberValidation("Sampling Rate is required"),
      evalsDetails: z
        .array(z.any())
        .min(1, { message: "At least one evaluation is required" })
        .refine(
          (evals) =>
            evals.every((e) => typeof e?.id === "string" && e.id.length > 0),
          {
            message:
              "Remove the highlighted evaluation(s) and re-add them before continuing.",
          },
        )
        .transform((evals) => evals.map((e) => e.id)),
      startDate: z.string(),
      endDate: z.string(),
      // Listed for the same reason as rowType below — zod strips unlisted keys.
      datePreset: z.string().optional(),
      runType: z.enum(["historical", "continuous"], {
        message: "Run Type is required",
      }),
      // Without listing rowType here, zod's .object() strips it before
      // the transform runs and the form-state value (set by the
      // Spans/Traces/Sessions tabs in TaskConfigPanel) is silently
      // dropped — every payload then defaults to "spans".
      rowType: z.enum(["spans", "traces", "sessions", "voiceCalls"]).optional(),
      filters: z
        .array(
          z.object({
            id: z.string().optional(),
            propertyId: z.string().optional(),
            registryId: z.string().optional(),
            property_id: z.string().optional(),
            property: z.string().optional(),
            fieldCategory: z.string().optional(),
            fieldLabel: z.string().optional(),
            apiColType: z.string().optional(),
            filterConfig: z
              .object({
                filterType: z.string().optional(),
                filterOp: z.any().optional(),
                filterValue: z.any().optional(),
                colType: z.string().optional(),
                attributeValueTypes: z
                  .array(z.enum(["string", "number", "boolean"]).nullable())
                  .optional(),
              })
              .optional(),
          }),
        )
        .optional(),
    })
    .refine(
      (data) => {
        if (data.runType === "historical") {
          return !!data.spansLimit;
        }
        return true;
      },
      {
        message: "Max Spans is required for historical runs",
        path: ["spansLimit"],
      },
    )
    .transform((data) => {
      const { filters, attributeFilters } =
        getNewTaskFilters(data, data?.project) ?? {};

      const finalData = {
        name: data?.name,
        project: data?.project,
        // The custom row-limit input yields a string; the API contract
        // requires spans_limit as an integer, and strict request-contract
        // validation aborts the POST on a string.
        spansLimit:
          data?.spansLimit != null && data?.spansLimit !== ""
            ? Number(data.spansLimit)
            : data?.spansLimit,
        samplingRate: data?.samplingRate,
        evals: data?.evalsDetails,
        runType: data?.runType,
        rowType: data?.rowType ?? "spans",
        filters: {
          ...filters,
          ...(attributeFilters && attributeFilters?.length > 0
            ? { filters: attributeFilters }
            : {}),
        },
      };

      return finalData;
    });
