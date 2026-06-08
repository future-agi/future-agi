import { z } from "zod";

export const userDataSchema = z
  .object({
    role: z.string().optional(),
    customRole: z.string().optional(),
    goals: z.array(z.boolean()).optional(),
  })
  .refine((data) => data.role?.trim() || data.customRole?.trim(), {
    message: "Please select a role or enter your role",
    path: ["role"],
  })
  .refine((data) => data.goals?.some(Boolean), {
    message: "Please select at least one goal",
    path: ["goals"],
  });

export const organizationSchema = z.object({
  orgName: z.string().min(1, "Organization name is required"),
  members: z
    .array(
      z.object({
        email: z.string().optional(),
        name: z.string().optional(),
        organization_role: z.string().min(1, "Role is required"),
        disabled: z.boolean().optional(),
      }),
    )
    .superRefine((members, ctx) => {
      const seen = new Map();

      members.forEach((member, index) => {
        const email = (member.email || "").trim().toLowerCase();
        if (!email) return;

        if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            message: "Invalid email format",
            path: [index, "email"],
          });
          return;
        }

        if (seen.has(email)) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            message: "Duplicate email not allowed",
            path: [index, "email"],
          });
        } else {
          seen.set(email, index);
        }
      });
    }),
});
