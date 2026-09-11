import { z } from "zod";
import { RANGE_FILTER_OPS } from "src/api/contracts/filter-contract.generated";

const RangeOperators = new Set(RANGE_FILTER_OPS);

export const NumberQuickFilterValidationSchema = z
  .object({
    operator: z.string(),
    value1: z.number().nonnegative("Required"),
    value2: z.number().nonnegative("Required"),
  })
  .superRefine((formValues, ctx) => {
    const operator = formValues.operator;

    if (RangeOperators.has(operator)) {
      if (formValues.value2 === undefined || formValues.value2 === null) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Required",
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
