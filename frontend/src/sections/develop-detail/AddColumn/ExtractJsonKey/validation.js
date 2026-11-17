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
    // v2.1 g8: an empty field says what it needs, not a type error.
    concurrency: z
      .number({
        invalid_type_error: "Enter how many rows run at once (a number)",
        required_error: "Enter how many rows run at once (a number)",
      })
      .int("Concurrency must be a whole number")
      .positive("Concurrency must be a positive integer"),
  });
};
