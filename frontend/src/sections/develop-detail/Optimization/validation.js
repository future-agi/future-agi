import { z } from "zod";

export const OptimizationCreateSchema = z.object({
  name: z.string().min(1, "Name is required"),
  columnId: z.string().min(1, "Column is required"),
  optimizeType: z.enum(["PROMPT_TEMPLATE"]),
  userEvalTemplateIds: z.array(z.any()).min(1, {
    message: "At least one evaluation is required",
  }),
});
