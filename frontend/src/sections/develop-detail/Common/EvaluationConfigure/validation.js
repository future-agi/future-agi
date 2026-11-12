import { camelCaseToTitleCase } from "src/utils/utils";
import { HtmlPromptValidationSchema } from "src/utils/validation";
import { z } from "zod";

export const encloseString = (string, options) => {
  return `${options.rulePromptStartEnclosures}${string}${options.rulePromptEndEnclosures}`;
};

// Generate zod validation schema from evalConfig
export const generateValidationSchema = (
  evalConfig,
  requiredColumnIds = [],
  allColumns,
  options = { rulePromptStartEnclosures: "{{", rulePromptEndEnclosures: "}}" },
) => {
  const mappingSchema = {};
  const configSchema = {};

  const optionalKeys = evalConfig?.optionalKeys || [];
  const variableKeys = evalConfig?.variableKeys || [];

  // Add validation for required keys
  for (const key of evalConfig?.requiredKeys || []) {
    const isMultipleColumn = variableKeys.includes(key);
    mappingSchema[key] = isMultipleColumn ? z.array(z.string()) : z.string();
    if (optionalKeys.includes(key)) {
      mappingSchema[key] = mappingSchema[key].transform((val) =>
        val.length ? val : null,
      );
    } else {
      mappingSchema[key] = mappingSchema[key].min(1, `Please select a column`);
    }
  }

  let isRulePrompt = false;

  // Add validation for config fields
  for (const [key, field] of Object.entries(evalConfig?.config || {})) {
    switch (field.type) {
      case "string":
        configSchema[key] = z
          .string({
            invalid_type_error: `${camelCaseToTitleCase(key)} is required.`,
          })
          .min(1, `${camelCaseToTitleCase(key)} is required.`);
        break;
      case "integer":
        configSchema[key] = z
          .string()
          .min(1, `${camelCaseToTitleCase(key)} is required.`)
          .transform((val) => Number.parseInt(val))
          .pipe(
            z
              .number({
                invalid_type_error: `${camelCaseToTitleCase(key)} is required.`,
              })
              .int(),
          );
        break;
      case "float":
        configSchema[key] = z
          .string()
          .min(1, `${camelCaseToTitleCase(key)} is required.`)
          .transform((val) => Number.parseFloat(val))
          .pipe(
            z.number({
              invalid_type_error: `${camelCaseToTitleCase(key)} is required.`,
            }),
          );
        break;
      case "boolean":
        configSchema[key] = z.boolean();
        break;
      case "option":
        configSchema[key] = z
          .string({
            invalid_type_error: `${camelCaseToTitleCase(key)} is required.`,
          })
          .min(1, `${camelCaseToTitleCase(key)} is required.`);
        break;
      case "dict":
        configSchema[key] = z.union([
          z
            .array(
              z.object({
                key: z.string().min(1, "Key is required"),
                value: z.string().min(1, "Value is required"),
              }),
            )
            .transform((arr) => {
              return arr.reduce(
                (acc, { key, value }) => ({
                  ...acc,
                  [key]: value,
                }),
                {},
              );
            }),
          z.record(z.string(), z.string()),
        ]);
        break;
      case "prompt":
        configSchema[key] = HtmlPromptValidationSchema.refine(
          (str) => str.length > 1,
          "Prompt is required",
        ).transform((str) => {
          if (str) {
            return allColumns?.reduce((acc, col) => {
              return acc.replace(
                new RegExp(`{{${col.headerName}}}`, "g"),
                `{{${col.field}}}`,
              );
            }, str);
          } else {
            return str;
          }
        });
        // .superRefine((mapping, ctx) => {
        //   console.log("mapping: ",configSchema[key], key, mapping)
        //   requiredColumnIds.forEach((columnId) => {
        //     if (!mapping.includes(columnId)) {
        //       ctx.addIssue({
        //         code: z.ZodIssueCode.custom,
        //         message: `Base column must be mapped`,
        //         path: [""],
        //       });
        //     }
        //   });
        // });
        break;
      case "list":
        configSchema[key] = z
          .string()
          .min(1, `${camelCaseToTitleCase(key)} is required.`)
          .transform((val) => val.split(",").map((item) => item.trim()))
          .pipe(
            z
              .array(z.string())
              .min(
                1,
                `${camelCaseToTitleCase(key)} must have at least one item`,
              ),
          );
        break;
      case "code":
        configSchema[key] = z
          .string({ invalid_type_error: "Code is required" })
          .min(1, "Code is required")
          .transform((val) => {
            return allColumns?.reduce((acc, col) => {
              const newVal = `{{${col.field}}}`;
              // if (
              //   !col?.dataType ||
              //   col?.dataType === "boolean" ||
              //   col?.dataType === "float" ||
              //   col?.dataType === "integer"
              // ) {
              //   newVal = `{{${col.field}}}`;
              // } else {
              //   newVal = `'{{${col.field}}}'`;
              // }
              return acc.replace(
                new RegExp(`{{${col.headerName}}}`, "g"),
                newVal,
              );
            }, val);
          });
        break;
      case "rule_string":
        configSchema[key] = z
          .array(
            z.object({
              id: z.string(),
              value: z.string().min(1, "Please select a column"),
            }),
          )
          .min(1, "Inputs is required")
          .transform((arr) =>
            arr.map((item) => encloseString(item.value, options)),
          )
          .superRefine((mapping, ctx) => {
            requiredColumnIds.forEach((columnId) => {
              // console.log("mappid", columnId, mapping);
              if (!mapping.includes(`{{${columnId}}}`)) {
                ctx.addIssue({
                  code: z.ZodIssueCode.custom,
                  message: `Base column must be selected`,
                  path: [],
                });
              }
            });
          });
        break;
      case "choices":
        configSchema[key] = z
          .array(
            z.object({
              id: z.string(),
              value: z.string(),
            }),
          )
          .min(1, "Choices are required")
          .transform((arr) => arr.map((item) => item.value));
        break;
      case "rule_prompt": {
        configSchema[key] = HtmlPromptValidationSchema.refine(
          (str) => str.length > 1,
          "Rule Prompt is required",
        ).transform((str) => {
          return allColumns?.reduce((acc, col) => {
            return acc.replace(
              new RegExp(`{{${col.headerName}}}`, "g"),
              `{{${col.field}}}`,
            );
          }, str);
        });
        isRulePrompt = true;
        break;
      }
      default:
        configSchema[key] = z.any();
    }
  }

  let config = z.object(configSchema).superRefine((mapping, ctx) => {
    const keys = Object.keys(mapping);
    if (
      keys.length > 0 &&
      keys.some((key) => key === "evalPrompt" || key === "systemPrompt") &&
      requiredColumnIds.length > 0
    ) {
      let matched = false;
      Object.entries(mapping).forEach(([key, value]) => {
        if (key === "evalPrompt" || key === "systemPrompt") {
          const newVal = allColumns?.reduce((acc, col) => {
            return acc.replace(
              new RegExp(`{{${col.headerName}}}`, "g"),
              `{{${col.field}}}`,
            );
          }, value);
          requiredColumnIds.forEach((columnId) => {
            if (newVal.includes(columnId)) {
              matched = true;
            }
          });
        }
      });
      if (!matched && keys.length > 0) {
        Object.keys(mapping).forEach((key) => {
          if (key === "evalPrompt" || key === "systemPrompt") {
            ctx.addIssue({
              code: z.ZodIssueCode.custom,
              message: `The config column must be one of chosen values`,
              path: [key],
            });
          }
        });
      }
    }
  });

  if (isRulePrompt) {
    const ruleStringConfig = Object.entries(evalConfig?.config).find(
      ([_, fc]) => {
        return fc.type === "rule_string";
      },
    );
    const rulePromptConfig = Object.entries(evalConfig?.config).find(
      ([_, fc]) => {
        return fc.type === "rule_prompt";
      },
    );
    config = config
      .superRefine((obj, ctx) => {
        const rulePromptKey = rulePromptConfig?.[0];
        const ruleStringArray = obj[ruleStringConfig?.[0]];
        const rulePromptString = obj[rulePromptKey];
        // Check if all rule string IDs are present in rule prompt
        ruleStringArray?.forEach((ruleId) => {
          if (!rulePromptString?.includes(ruleId)) {
            ctx.addIssue({
              code: z.ZodIssueCode.custom,
              message: `Rule prompt must contain all selected columns`,
              path: [rulePromptKey],
            });
          }
        });
      })
      .transform((obj) => {
        if (!rulePromptConfig || !rulePromptConfig) return obj;

        const rulePromptKey = rulePromptConfig?.[0];
        const ruleStringKey = ruleStringConfig?.[0];
        const ruleStringArray = obj[ruleStringKey];
        const rulePromptString = obj[rulePromptKey];

        const newRulePromptString = ruleStringArray.reduce(
          (acc, curr, index) => {
            const id = curr.replace("{{", "").replace("}}", "");
            acc = acc.replaceAll(`{{${id}}}`, `{{variable_${index + 1}}}`);
            return acc;
          },
          rulePromptString,
        );

        return { ...obj, [rulePromptKey]: newRulePromptString };
      });
  }

  return z.object({
    name: z.string().min(1, "Name is required"),
    description: z.string().optional(),
    saveAsTemplate: z.boolean(),
    config: z.object({
      mapping: z.object(mappingSchema).superRefine((mapping, ctx) => {
        requiredColumnIds.forEach((columnId) => {
          if (!Object.values(mapping).includes(columnId)) {
            // Add issue for each mapping key individually
            for (const key in mapping) {
              ctx.addIssue({
                code: z.ZodIssueCode.custom,
                message: `The config column must be one of chosen values`,
                path: [key],
              });
            }
          }
        });
      }),
      config,
      reasonColumn: z.boolean(),
    }),
  });
};
