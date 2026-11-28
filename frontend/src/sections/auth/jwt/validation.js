import z from "zod";

export const ForgotPasswordSchema = z.object({
  email: z.preprocess(
    (val) => (typeof val === "string" ? val.trim() : val),
    z.string().email({ message: "Please provide a valid email address" }),
  ),
});

export const ResetPasswordSchema = z
  .object({
    newPassword: z
      .string()
      .min(8, "Password should be 8 characters long")
      .max(128, "Password must not exceed 128 characters")
      .regex(/[a-zA-Z]/, "Password must contain at least one letter")
      .regex(
        /[\d\p{P}\p{S}]/u,
        "Password must contain at least one number or special character",
      ),
    repeatPassword: z.string().min(8, "Password should be 8 characters long"),
  })
  .refine(({ newPassword, repeatPassword }) => newPassword === repeatPassword, {
    message: "Passwords don't match",
    path: ["repeatPassword"],
  });
