import { expect, it } from "vitest";
import { ResetPasswordSchema } from "../validation";

it("counts punctuation and symbols, but not whitespace, in a password without digits", () => {
  const accepts = (newPassword) =>
    ResetPasswordSchema.safeParse({ newPassword, repeatPassword: newPassword })
      .success;
  for (const symbol of ["/", "\\", "~", "`", "'", '"', "€"]) {
    expect(accepts(`Cedar${symbol}Meadow`)).toBe(true);
  }
  expect(accepts("Cedar Meadow")).toBe(false);
});
