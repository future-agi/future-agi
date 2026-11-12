import { z } from "zod";
import { replaceColumnIdWithName, replaceColumnNameWithId } from "../common";
import { getRandomId } from "src/utils/utils";

export const _transformDynamicColumnConfig = (type, config, allColumns) => {
  switch (type) {
    case "api_call":
      return {
        config: {
          ...config.config,
          body: replaceColumnIdWithName(
            JSON.stringify(config?.config?.body) || "",
            allColumns,
          ),
          params: Object.entries(config?.config?.params || {}).map(
            ([key, value]) => ({
              id: getRandomId(),
              name: key,
              value: value.value,
              type: value.type,
            }),
          ),
          headers: Object.entries(config?.config?.headers || {}).map(
            ([key, value]) => ({
              id: getRandomId(),
              name: key,
              value: value.value,
              type: value.type,
            }),
          ),
        },
        concurrency: config?.concurrency,
      };
    case "classification": {
      const { labels, ...rest } = config;
      return {
        ...rest,
        labels: labels?.map((item) => ({
          id: getRandomId(),
          value: item?.value ?? item,
        })),
      };
    }
    default:
      return config;
  }
};

export const getConditionalNodeDefaultValues = (initialData, allColumns) => {
  if (initialData) {
    return {
      newColumnName: initialData.new_column_name,
      config: initialData.config?.map((con) => ({
        branchType: con.branch_type,
        condition: replaceColumnIdWithName(con.condition ?? "", allColumns),
        branchNodeConfig: {
          type: con.branch_node_config?.type ?? "",
          config: con.branch_node_config?.config ?? null,
        },
      })),
    };
  }

  return {
    newColumnName: "",
    config: [
      {
        branchType: "if",
        condition: "",
        branchNodeConfig: {
          type: "",
          config: null,
        },
      },
    ],
  };
};

export const getConditionalNodeValidation = (allColumns, isEdit = false) => {
  return z.object({
    newColumnName: isEdit
      ? z.string().optional()
      : z.string().min(1, { message: "New column name is required" }),
    config: z.array(
      z
        .object({
          branchType: z.enum(["if", "elif", "else"]),
          // L5 G4: an Else branch has no condition.
          condition: z
            .string()
            .optional()
            .default("")
            .transform((val) => replaceColumnNameWithId(val ?? "", allColumns)),
          branchNodeConfig: z.object({
            type: z.string().min(1, { message: "Type is required" }),
            config: z
              .any()
              .nullable()
              .refine((val) => val !== null, {
                message: "Config is required",
              }),
          }),
        })
        .superRefine((branch, ctx) => {
          if (branch.branchType !== "else" && !String(branch.condition).trim())
            ctx.addIssue({
              code: z.ZodIssueCode.custom,
              path: ["condition"],
              message: "Condition is required",
            });
        }),
    ),
  });
};
