import { z } from "zod";

export const ClassificationValidationSchema = (
  isConditionalNode = false,
  isEdit = false,
) => {
  return z.object({
    column_id: z
      .string({
        required_error: "Column is required",
      })
      .min(1, {
        message: "Column is required",
      }),
    labels: z
      .array(
        z.object({
          id: z.string(),
          value: z.string().min(1, "Please enter a label"),
        }),
      )
      .min(1, "At least one label is required")
      .transform((t) => t.map((e) => e.value)),
    language_model_id: z
      .string({
        required_error: "Language Model is required",
      })
      .min(1, {
        message: "Language Model is required",
      }),
    concurrency: z.number().positive("Concurrency must be a positive integer"),
    new_column_name:
      isConditionalNode || isEdit
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
