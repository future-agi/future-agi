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

// Single-page feedback schema: a right value, an improvement note, and a
// re-tune action must all be provided before the feedback can be submitted.
// Each field carries a single, friendly "please fill this" message so the
// always-active submit button can surface them inline on click.
const isFilledValue = (value) => {
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "number") return !Number.isNaN(value);
  if (typeof value === "string") return value.trim().length > 0;
  return value !== null && value !== undefined;
};

export const feedbackFormSchema = z.object({
  value: z.any().refine(isFilledValue, {
    message: "Please select or enter a right value.",
  }),
  explanation: z
    .string({ required_error: "Please tell us what you'd like to improve." })
    .trim()
    .min(1, { message: "Please tell us what you'd like to improve." }),
  actionType: z.enum(["retune", "recalculate_row", "recalculate_dataset"], {
    errorMap: () => ({ message: "Please select one of the options." }),
  }),
});
