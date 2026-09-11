import { z } from "zod";

export const ExtractJsonKeyValidationSchema = (
  isConditionalNode = false,
  isEdit = false,
) => {
  return z.object({
    column_id: z.string().min(1, "Column is required"),
    json_key: z.string().min(1, "JSON Key is required"),
    new_column_name:
      isConditionalNode || isEdit
        ? z.string().optional()
        : z.string().min(1, "New Column Name is required"),
    concurrency: z.number().positive("Concurrency must be a positive integer"),
  });
};
