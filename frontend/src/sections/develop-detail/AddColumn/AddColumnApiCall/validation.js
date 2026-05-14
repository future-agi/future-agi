import { z } from "zod";

const BARE_VARIABLE_RE = /^\s*\{\{[^}]+\}\}\s*$/;

export const getAddColumnApiCallValidation = (
  allColumns,
  isConditionalNode = false,
  isEdit = false,
) => {
  return z.object({
    type: z.string().optional(),
    columnName:
      isConditionalNode || isEdit
        ? z.string().optional()
        : z.string().min(1, "Name is required"),
    config: z.object({
      url: z
        .string()
        .min(1, "URL is required")
        .transform((v) => {
          let content = v;
          allColumns.forEach(({ headerName, field }) => {
            const pattern = new RegExp(`{{\\s*${headerName}((?:\\.[^}\\s]+|\\[\\d+\\])*)\\s*}}`, "g");
            content = content.replace(pattern, `{{${field}$1}}`);
          });
          return content;
        }),
      method: z.string().min(1, "Method is required"),
      params: z
        .array(
          z.object({
            id: z.string(),
            name: z.string().min(1, "Key is required"),
            value: z.string().min(1, "Value is required"),
            type: z.string().min(1, "Type is required"),
          }),
        )
        .transform((arr) => {
          return arr.reduce((acc, curr) => {
            let val = curr.value;
            if (curr.type === "Variable") {
              allColumns.forEach(({ headerName, field }) => {
                const p = new RegExp(`{{\\s*${headerName}((?:\\.[^}\\s]+|\\[\\d+\\])*)\\s*}}`, "g");
                val = val.replace(p, `{{${field}$1}}`);
              });
            }
            acc[curr.name] = { type: curr.type, value: val };
            return acc;
          }, {});
        }),
      headers: z
        .array(
          z.object({
            id: z.string(),
            name: z.string().min(1, "Key is required"),
            value: z.string().min(1, "Value is required"),
            type: z.string().min(1, "Type is required"),
          }),
        )
        .transform((arr) => {
          return arr.reduce((acc, curr) => {
            let val = curr.value;
            if (curr.type === "Variable") {
              allColumns.forEach(({ headerName, field }) => {
                const p = new RegExp(`{{\\s*${headerName}((?:\\.[^}\\s]+|\\[\\d+\\])*)\\s*}}`, "g");
                val = val.replace(p, `{{${field}$1}}`);
              });
            }
            acc[curr.name] = { type: curr.type, value: val };
            return acc;
          }, {});
        }),
      body: z
        .string()

        .transform((v) => {
          let content = v;

          allColumns.forEach(({ headerName, field }) => {
            const pattern = new RegExp(`{{\\s*${headerName}((?:\\.[^}\\s]+|\\[\\d+\\])*)\\s*}}`, "g");
            content = content.replace(pattern, `{{${field}$1}}`);
          });

          return content;
        })
        .refine((value) => {
          if (!value?.length) return true;
          if (BARE_VARIABLE_RE.test(value)) return true;
          try {
            JSON.parse(value);
            return true;
          } catch (e) {
            return false;
          }
        }, "Invalid JSON format")
        .transform((value) => {
          if (!value?.length) return {};
          if (BARE_VARIABLE_RE.test(value)) return value;
          return JSON.parse(value);
        }),
      outputType: z.string().min(1, "Output type is required"),
    }),
    concurrency: z.number().positive("Concurrency must be a positive integer"),
  });
};
