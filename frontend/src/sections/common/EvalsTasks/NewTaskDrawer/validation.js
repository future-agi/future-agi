import { getNumberValidation } from "src/utils/validation";
import { z } from "zod";
import {
  ANNOTATION_COLUMN_IDS,
  FIELD_CATEGORY_TO_COL_TYPE,
  RANGE_OPS,
  LIST_OPS,
  NO_VALUE_OPS,
} from "src/sections/common/EvalsTasks/common";

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
  return (filters || [])
    .filter((f) => {
      if (!f) return false;
      // Sibling keys are emitted separately by getNewTaskFilters.
      if (f.property in TOP_LEVEL_SIBLING_KEY_BY_PROPERTY) return false;
      // Legacy rows with neither apiColType nor propertyId are BE no-ops.
      if (!f.propertyId && f.property !== "attributes") return false;
      return true;
    })
    .map((f) => {
      const columnId = f.propertyId || f.property;
      const op = f?.filterConfig?.filterOp || "equals";
      const filterType = f?.filterConfig?.filterType || "text";
      const v = f?.filterConfig?.filterValue;

      // Resolution: pinned ANNOTATION ids → row.apiColType (canonical) →
      // fieldCategory fallback → SPAN_ATTRIBUTE default.
      let apiColType;
      if (ANNOTATION_COLUMN_IDS.has(columnId)) {
        apiColType = "ANNOTATION";
      } else if (f?.apiColType) {
        apiColType = f.apiColType;
      } else if (FIELD_CATEGORY_TO_COL_TYPE[f?.fieldCategory]) {
        apiColType = FIELD_CATEGORY_TO_COL_TYPE[f.fieldCategory];
      } else {
        apiColType = "SPAN_ATTRIBUTE";
      }

      let filterValue;
      if (NO_VALUE_OPS.has(op)) {
        filterValue = "";
      } else if (RANGE_OPS.has(op)) {
        if (Array.isArray(v) && v.length > 0) filterValue = v;
      } else if (LIST_OPS.has(op)) {
        const arr = Array.isArray(v)
          ? v
          : v !== undefined && v !== null && v !== ""
            ? [v]
            : [];
        if (arr.length > 0) filterValue = arr;
      } else if (v !== undefined && v !== null && v !== "") {
        filterValue = v;
      }

      return {
        column_id: columnId,
        filter_config: {
          filter_type: filterType,
          filter_op: op,
          col_type: apiColType,
          ...(filterValue !== undefined && { filter_value: filterValue }),
        },
      };
    })
    // Drop value-less in/not_in (legacy/hand-edited)
    .filter(
      (entry) =>
        !LIST_OPS.has(entry.filter_config.filter_op) ||
        entry.filter_config.filter_value !== undefined,
    );
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
    filters["date_range"] = [
      new Date(data?.startDate).toISOString(),
      new Date(data?.endDate).toISOString(),
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
        spansLimit: data?.spansLimit,
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
