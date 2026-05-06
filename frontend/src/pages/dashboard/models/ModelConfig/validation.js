import { z } from "zod";

export const ConfigureDefaultDatasetValidation = z.object({
  environment: z
    .string({
      required_error: "Environment is required.",
      invalid_type_error: "Environment is required.",
    })
    .min(1, "Environment is required."),
  modelVersion: z
    .string({
      required_error: "Model Version is required.",
      invalid_type_error: "Model Version is required.",
    })
    .min(1, "Model Version is required."),
});
