import { z } from "zod";

export const AddRowUsingAiValidationSchema = z.object({
  description: z
    .string({ message: "Description is required" })
    .min(1, "Description is required"),
  numRows: z
    .number({ message: "Rows is required" })
    .min(10, "Enter at least 10 rows")
    .max(20000, "Enter 20,000 rows or fewer"),
});
