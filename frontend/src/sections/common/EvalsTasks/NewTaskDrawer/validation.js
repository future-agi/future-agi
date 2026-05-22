import { getNumberValidation } from "src/utils/validation";
import { z } from "zod";

const RANGE_OPS = new Set(["between", "not_between"]);
const LIST_OPS = new Set(["in", "not_in"]);
const TASK_FILTER_PROPERTY_TO_API = {
  span_kind: "observation_type",
  observation_type: "observation_type",
};

export const getTaskFilterApiKey = (property) =>
  TASK_FILTER_PROPERTY_TO_API[property] || property;

// One form row → one wire entry. Cross-row composition is the BE's
// responsibility; merging here would collapse "not_contains A AND
// not_contains B" into "in [A, B]" and invert the user's intent.
export const extractAttributeFilters = (filters) => {
  return (filters || []).reduce((acc, f) => {
    if (f?.property !== "attributes") return acc;
    const columnId = f.propertyId;
    if (!columnId) return acc;
    const op = f?.filterConfig?.filterOp || "equals";
    const filterType = f?.filterConfig?.filterType || "text";
    const v = f?.filterConfig?.filterValue;

    let filterValue;
    if (RANGE_OPS.has(op) || LIST_OPS.has(op)) {
      if (Array.isArray(v) && v.length > 0) filterValue = v;
    } else if (v !== undefined && v !== null && v !== "") {
      filterValue = v;
    }
    acc.push({
      column_id: columnId,
      filter_config: {
        filter_type: filterType,
        filter_op: op,
        col_type: "SPAN_ATTRIBUTE",
        ...(filterValue !== undefined && { filter_value: filterValue }),
      },
    });
    return acc;
  }, []);
};

export const getNewTaskFilters = (data, projectId, ignoreDate = false) => {
  const filters = { project_id: projectId?.length ? projectId : null };

  const attributeFilters = extractAttributeFilters(data?.filters);

  // System filters: spread array `filterValue` (from canonical `in`/`not_in`
  // or `between` rows) into the per-field array so the BE wire stays in the
  // historical `{ field: [v1, v2, ...] }` shape it expects.
  data?.filters?.forEach((filter) => {
    if (filter?.property === "attributes") return;
    const apiKey = getTaskFilterApiKey(filter?.property);
    if (!apiKey) return;
    const val = filter?.filterConfig?.filterValue;
    const vals = Array.isArray(val)
      ? val
      : val !== undefined && val !== null && val !== ""
        ? [val]
        : [];
    if (vals.length === 0) return;
    if (apiKey in filters) {
      filters[apiKey].push(...vals);
    } else {
      filters[apiKey] = [...vals];
    }
  });

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
            filterConfig: z
              .object({
                filterType: z.string().optional(),
                filterOp: z.any().optional(),
                filterValue: z.any().optional(),
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
            ? { span_attributes_filters: attributeFilters }
            : {}),
        },
      };

      return finalData;
    });
