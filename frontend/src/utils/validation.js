import { z } from "zod";

export const HtmlPromptValidationSchema = z
  .string({
    message: "This field is required",
    invalid_type_error: "This field is required",
  })
  .transform((str) =>
    str
      .replace(/<br\s*\/?>/gi, "\n")
      .replace(/<div\s*\/?>/gi, "\n")
      .replace(/<p\s*\/?>/gi, "\n")
      .replace(/<[^>]*>/g, "")
      .replace(/\n+/g, "\n")
      .trim(),
  );

export const getNumberValidation = (
  message = "This should be a valid number",
  options,
) => {
  const integerOnly =
    options?.integerOnly !== undefined ? options.integerOnly : false;

  const zeroAndPositive =
    options?.zeroAndPositive !== undefined ? options?.zeroAndPositive : false;
  const positive = options?.positive !== undefined ? options?.positive : false;

  let schema = z
    .number({
      invalid_type_error: message,
      required_error: message,
    })
    .or(
      z
        .string()
        .refine((str) => str.length >= 1 && !isNaN(Number(str)), message)
        .transform(Number),
    );

  if (integerOnly) {
    schema = schema.refine((num) => Number.isInteger(num), {
      message: "Value must be an integer (no decimal places)",
    });
  }

  if (positive) {
    schema = schema.refine((num) => num > 0, {
      message: "Value must be positive",
    });
  }

  if (zeroAndPositive) {
    schema = schema.refine((num) => num >= 0, {
      message: "Value must be positive or zero",
    });
  }

  return schema;
};

export const ColumnNameValidationSchema = z
  .string()
  .min(1, "Column name is required")
  .max(50, "Column name must be less than 50 characters")
  .regex(
    /^[a-z0-9_-]+$/,
    "Only lowercase letters, numbers, underscores, and hyphens are allowed (no spaces)",
  );
