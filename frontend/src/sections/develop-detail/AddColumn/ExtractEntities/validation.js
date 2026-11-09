import { z } from "zod";

export const ExtractEntitiesValidationSchema = (
  isConditioalNode = false,
  isEdit,
) => {
  return z.object({
    type: z.string().optional(),
    column_id: z
      .string({
        required_error: "Column ID is required",
      })
      .min(1, {
        message: "Column is required",
      }),
    instruction: z
      .string({
        required_error: "Instruction is required",
      })
      .min(1, {
        message: "Instruction is required",
      }),
    language_model_id: z
      .string({
        required_error: "Language Model is required",
      })
      .min(1, {
        message: "Language Model is required",
      }),
    // v2.1 g8: an empty field says what it needs, not a type error.
    concurrency: z
      .number({
        invalid_type_error: "Enter how many rows run at once (a number)",
        required_error: "Enter how many rows run at once (a number)",
      })
      .int("Concurrency must be a whole number")
      .positive("Concurrency must be a positive integer"),
    new_column_name:
      isConditioalNode || isEdit
        ? z.string().optional()
        : z
            .string({
              required_error: "New Column Name is required",
            })
            .min(1, {
              message: "New Column Name is required",
            }),
  });
};
