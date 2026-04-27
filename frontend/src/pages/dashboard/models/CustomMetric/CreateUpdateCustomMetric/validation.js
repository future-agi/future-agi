import { z } from "zod";

export const CreateMetricFormValidation = z.object({
  name: z
    .string({ required_error: "Metric Name is required." })
    .min(1, "Metric Name is required."),
  prompt: z
    .string({ required_error: "Please define a metric." })
    .min(1, "Please define a metric."),
  datasets: z
    .array(
      z.object({
        environment: z.string(),
        modelVersion: z.string(),
      }),
      {
        required_error: "Please select a environment and version.",
        message: "Please select a environment and version.",
      },
    )
    .min(1, "Please select a environment and version."),
  metricType: z.union([z.literal(1), z.literal(2)], {
    message: "Please select model type.",
    required_error: "Please select model type.",
  }),
  evaluationType: z
    .string({
      required_error: "Please select a option",
      invalid_type_error: "Please select a option",
    })
    .min(1, "Please select a option"),
});
