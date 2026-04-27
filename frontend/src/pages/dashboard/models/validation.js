import { z } from "zod";

export const CreateModelFormValidation = z.object({
  modelName: z
    .string({ required_error: "Model Name is required." })
    .min(1, "Model Name is required."),
  modelTypeId: z.number({
    invalid_type_error: "Model Type is required.",
    required_error: "Model Type is required",
  }),
});
