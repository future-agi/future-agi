import { z } from "zod";
import { RANGE_FILTER_OPS } from "src/api/contracts/filter-contract.generated";

const RangeOperators = new Set(RANGE_FILTER_OPS);

// "Required" is only for a field with no number in it. No column this filter
// reads holds a number below 0, so a negative gets its own sentence.
const filterNumber = z
  .number({ required_error: "Required", invalid_type_error: "Required" })
  .nonnegative("Enter a value of 0 or more");

export const NumberQuickFilterValidationSchema = z
  .object({
    operator: z.string(),
    value1: filterNumber,
    // Max is checked only while a range operator shows it, so a value left in
    // a hidden Max never blocks Apply without a word.
    value2: z.unknown(),
  })
  .superRefine((formValues, ctx) => {
    const operator = formValues.operator;

    if (RangeOperators.has(operator)) {
      const max = filterNumber.safeParse(formValues.value2);
      if (!max.success) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: max.error.issues[0].message,
          path: ["value2"],
        });
      } else if (
        (formValues.value1 !== undefined || formValues.value1 !== null) &&
        formValues.value2 <= formValues.value1
      ) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: `Enter a value more than ${formValues.value1}`,
          path: ["value2"],
        });
      }
    }
  });
