import { getNumberValidation } from "src/utils/validation";
import { z } from "zod";

const RANGE_OPS = new Set(["between", "not_between"]);
const LIST_OPS = new Set(["in", "not_in"]);

// Group multiple form rows for the same (columnId, op) into a single wire
// entry. Scalar rows for list ops collapse to array `filterValue`; multiple
// scalar rows for a single-value op (legacy multi-value `equals` from saved
// tasks) are promoted to `in` so the BE filter validator accepts them.
export const extractAttributeFilters = (filters) => {
  const merged = new Map();
  (filters || [])
    .filter((f) => f?.property === "attributes")
    .forEach((f) => {
      const columnId = f.propertyId;
      if (!columnId) return;
      const op = f?.filterConfig?.filterOp || "equals";
      const filterType = f?.filterConfig?.filterType || "text";
      const key = `${columnId}|${op}|${filterType}`;
      if (!merged.has(key)) {
        merged.set(key, {
          columnId,
          op,
          filterType,
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
        colType: "SPAN_ATTRIBUTE",
        ...(filterValue !== undefined && { filterValue }),
      },
    };
  });
};

export const getNewTaskFilters = (data, projectId, ignoreDate = false) => {
  const filters = { project_id: projectId?.length ? projectId : null };

  const attributeFilters = extractAttributeFilters(data?.filters);

  // System filters: spread array `filterValue` (from canonical `in`/`not_in`
  // or `between` rows) into the per-field array so the BE wire stays in the
  // historical `{ field: [v1, v2, ...] }` shape it expects.
  data?.filters?.forEach((filter) => {
    if (filter?.property === "attributes") return;
    const val = filter?.filterConfig?.filterValue;
    const vals = Array.isArray(val)
      ? val
      : val !== undefined && val !== null && val !== ""
        ? [val]
        : [];
    if (vals.length === 0) return;
    if (filter?.property in filters) {
      filters[filter?.property].push(...vals);
    } else {
      filters[filter?.property] = [...vals];
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
            evals.every(
              (e) => typeof e?.id === "string" && e.id.length > 0,
            ),
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
      rowType: z
        .enum(["spans", "traces", "sessions", "voiceCalls"])
        .optional(),
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
