import { getNumberValidation } from "src/utils/validation";
import { z } from "zod";

export const extractAttributeFilters = (filters) => {
  const attributeFilters = filters
    ?.filter((filter) => filter?.property === "attributes")
    .map(({ property, propertyId, id, ...rest }) => ({
      ...rest,
      columnId: propertyId,
      filterConfig: {
        ...rest?.filterConfig,
        colType: "SPAN_ATTRIBUTE",
      },
    }));
  return attributeFilters ?? [];
};

export const getNewTaskFilters = (data, projectId, ignoreDate = false) => {
  const filters = { project_id: projectId?.length ? projectId : null };

  const attributeFilters = extractAttributeFilters(data?.filters);

  data?.filters?.forEach((filter) => {
    if (filter?.property in filters) {
      if (filter?.property === "attributes") return;
      filters[filter?.property].push(filter?.filterConfig?.filterValue);
    } else {
      if (filter?.property === "attributes") return;
      filters[filter?.property] = [filter?.filterConfig?.filterValue];
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
        .transform((evals) => evals?.map((e) => e.id)),
      startDate: z.string(),
      endDate: z.string(),
      runType: z.enum(["historical", "continuous"], {
        message: "Run Type is required",
      }),
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
