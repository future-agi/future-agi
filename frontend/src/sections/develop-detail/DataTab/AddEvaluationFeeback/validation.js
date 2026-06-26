import { z } from "zod";

export const AddFeedbackValidationSchema = z.object({
  value: z
    .string({
      required_error: "Value is required",
    })
    .min(1, {
      message: "Value is required",
    }),
  explanation: z
    .string({
      required_error: "Explanation is required",
    })
    .min(1, {
      message: "Explanation is required",
    }),
});

export const feedbackSubmittedValidationSchema = z.object({
  value: z
    .string({
      required_error: "Value is required",
    })
    .min(1, {
      message: "Value is required",
    }),
});
