import { z } from "zod";

export const UploadFileValidationSchema = z.object({
  file: z.any().refine((val) => val !== null, {
    message: "File is required",
  }),
});

export const emptySetValidationSchema = z.object({
  row: z.coerce.number().int().min(1, { message: "Row must be at least 1" }),
});

export const HuggingFaceDatasetValidationSchema = z.object({});

// export const existingDatasetValidationSchema = (datasetId) =>
//   z.object({
//     name: z.string().refine(
//       (value) => (!datasetId ? value?.trim().length > 0 : true), // Conditional validation based on `datasetId`
//       { message: "Name is required" },
//     ),
//     value: z.string().min(1, "Value is required"), // `value` is required
//     dataType: z.string().min(1, "Data type is required"), // `dataType` is required
//     config: z.object({
//       mapping: z
//         .record(z.string().min(1,"This Field is required")) // Keys are strings, values are strings
//         .refine(
//           (mapping) =>
//             Object.values(mapping).some((value) => value.trim().length > 0),
//           "At least one mapping value must be present",
//         ),
//     }),
//   });

export const existingDatasetValidationSchema = (datasetId, checkboxHandle) =>
  z.object({
    name: z
      .string()
      .refine((value) => (!datasetId ? value?.trim().length > 0 : true), {
        message: "Name is required",
      }),
    value: z.string().min(1, "Value is required"),
    dataType: z.string().min(1, "Data type is required"),
    config: z.object({
      mapping: z
        .record(z.string())
        .superRefine((mapping, ctx) => {
          const allTrimmedValues = {};
          const checkedKeys = new Set();
          const duplicates = new Set();

          // First, collect all trimmed values and which keys are checked
          for (const [key, value] of Object.entries(mapping)) {
            const trimmed = value.trim();
            allTrimmedValues[key] = trimmed;

            if (checkboxHandle?.[key]) {
              checkedKeys.add(key);
            }
          }

          // Now, track duplicates only among checked fields
          const seenValues = {};

          for (const key of checkedKeys) {
            const trimmed = allTrimmedValues[key];

            // Check empty value
            if (!trimmed) {
              ctx.addIssue({
                code: z.ZodIssueCode.custom,
                message: "This field is required",
                path: [key],
              });
              continue;
            }

            if (seenValues[trimmed]) {
              // Duplicate found
              duplicates.add(trimmed);

              ctx.addIssue({
                code: z.ZodIssueCode.custom,
                message: "Name already exists",
                path: [key],
              });

              // Also add issue for the first occurrence (if not already marked)
              const firstKey = seenValues[trimmed];
              ctx.addIssue({
                code: z.ZodIssueCode.custom,
                message: "Name already exists",
                path: [firstKey],
              });
            } else {
              seenValues[trimmed] = key;
            }
          }
        })

        .refine(
          (mapping) =>
            Object.entries(mapping).some(([key, value]) =>
              checkboxHandle?.[key] ? value.trim().length > 0 : true,
            ),
          "At least one mapping value must be present",
        ),
    }),
  });
