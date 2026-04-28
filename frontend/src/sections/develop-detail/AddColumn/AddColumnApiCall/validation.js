import { z } from "zod";

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
            const pattern = new RegExp(`{{\\s*${headerName}\\s*}}`, "g");
            content = content.replace(pattern, `{{${field}}}`);
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
            acc[curr.name] = { type: curr.type, value: curr.value };
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
            acc[curr.name] = { type: curr.type, value: curr.value };
            return acc;
          }, {});
        }),
      body: z
        .string()

        .transform((v) => {
          let content = v;

          allColumns.forEach(({ headerName, field }) => {
            const pattern = new RegExp(`{{\\s*${headerName}\\s*}}`, "g");
            content = content.replace(pattern, `{{${field}}}`);
          });

          return content;
        })
        .refine((value) => {
          if (!value?.length) return true;
          try {
            JSON.parse(value);
            return true;
          } catch (e) {
            return false;
          }
        }, "Invalid JSON format")
        .transform((value) => {
          if (!value?.length) return {};
          return JSON.parse(value);
        }),
      outputType: z.string().min(1, "Output type is required"),
    }),
    concurrency: z.number().positive("Concurrency must be a positive integer"),
  });
};
