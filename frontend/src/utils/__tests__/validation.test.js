import { describe, expect, it } from "vitest";
import { ColumnNameValidationSchema } from "../validation";

describe("ColumnNameValidationSchema", () => {
  it.each(["User Name", "name!@#", "   "])(
    "rejects invalid column name %j",
    (value) => {
      const result = ColumnNameValidationSchema.safeParse(value);

      expect(result.success).toBe(false);
      expect(result.error.issues[0].message).toContain("Only lowercase");
    },
  );

  it("rejects empty names and names over 50 characters", () => {
    expect(ColumnNameValidationSchema.safeParse("").success).toBe(false);
    expect(ColumnNameValidationSchema.safeParse("a".repeat(51)).success).toBe(
      false,
    );
  });

  it("accepts lowercase names with numbers, underscores, and hyphens", () => {
    expect(
      ColumnNameValidationSchema.safeParse("customer_1-name").success,
    ).toBe(true);
  });
});
