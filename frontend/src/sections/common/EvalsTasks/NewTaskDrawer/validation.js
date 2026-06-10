import { getNumberValidation } from "src/utils/validation";
import { z } from "zod";
import {
  ANNOTATION_COLUMN_IDS,
  FIELD_CATEGORY_TO_COL_TYPE,
} from "src/sections/common/EvalsTasks/common";

const RANGE_OPS = new Set(["between", "not_between"]);
const LIST_OPS = new Set(["in", "not_in"]);

// Form-row `property` → outer-filters sibling key the BE honors. `node_type`
// is a FE alias for `observation_type` (the eval-task handler can't resolve
// it directly), so route it via the dedicated sibling branch.
const TOP_LEVEL_SIBLING_KEY_BY_PROPERTY = {
  observation_type: "observation_type",
  node_type: "observation_type",
  session_id: "session_id",
};

// Merge form rows for the same (columnId, op) into one wire entry. Scalar
// rows for list ops fold into array `filterValue`; multiple scalars under a
// single-value op are promoted to `in`. Matches list_spans_observe's flat
// shape — every chip carries its own `col_type` and the BE dispatches.
export const extractAttributeFilters = (filters) => {
  const merged = new Map();
  (filters || [])
    .filter((f) => {
      if (!f) return false;
      // Sibling keys are emitted separately by getNewTaskFilters.
      if (f.property in TOP_LEVEL_SIBLING_KEY_BY_PROPERTY) return false;
      // Legacy rows with neither apiColType nor propertyId are BE no-ops.
      if (!f.propertyId && f.property !== "attributes") return false;
      return true;
    })
    .forEach((f) => {
      const columnId = f.propertyId || f.property;
      if (!columnId) return;
      const op = f?.filterConfig?.filterOp || "equals";
      const filterType = f?.filterConfig?.filterType || "text";
      const key = `${columnId}|${op}|${filterType}`;
      if (!merged.has(key)) {
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

        merged.set(key, {
          columnId,
          op,
          filterType,
          apiColType,
          rangeValue: undefined,
          values: [],
        });
      }
      const entry = merged.get(key);
      const v = f?.filterConfig?.filterValue;
      if (RANGE_OPS.has(op)) {
        entry.rangeValue = Array.isArray(v) ? v : entry.rangeValue;
      } else if (LIST_OPS.has(op)) {
        const arr = Array.isArray(v)
          ? v
          : v !== undefined && v !== null && v !== ""
            ? [v]
            : [];
        entry.values.push(...arr);
      } else if (v !== undefined && v !== null && v !== "") {
        entry.values.push(v);
      }
    });

  return Array.from(merged.values()).map((entry) => {
    let filterValue;
    let filterOp = entry.op;
    if (RANGE_OPS.has(filterOp)) {
      filterValue = entry.rangeValue;
    } else if (LIST_OPS.has(filterOp)) {
      filterValue = entry.values;
    } else if (entry.values.length > 1) {
      // Multiple scalar rows under a single-value op → promote to `in`.
      filterOp = "in";
      filterValue = entry.values;
    } else if (entry.values.length === 1) {
      filterValue = entry.values[0];
    }
    return {
      columnId: entry.columnId,
      filterConfig: {
        filterType: entry.filterType,
        filterOp,
        colType: entry.apiColType,
        ...(filterValue !== undefined && { filterValue }),
      },
    };
  });
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
      // Declared so zod's strip doesn't drop the tab-selected value pre-transform.
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
