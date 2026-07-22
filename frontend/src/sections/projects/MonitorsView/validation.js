import { z } from "zod";

const validationSchema = z.object({
  name: z.string().min(1, "Name is required"),
  metric: z.string().default("span_response_time"),
  thresholdType: z.enum([
    "greater_than",
    "less_than",
    "greater_or_equal_to",
    "less_than_or_equal_to",
    "equal",
  ]),
  thresholdValue: z.union([
    z.string().min(1, "Threshold value is required"),
    z.number(),
  ]),
  notificationEmails: z
    .array(z.object({ value: z.string().email("Invalid email address") }))
    .max(5, "Can add up to 5 emails")
    .min(1, "At least one email is required")
    .transform((value) => value.map((item) => item.value)),
});
export default validationSchema;
