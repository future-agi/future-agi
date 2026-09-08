import * as z from "zod/v4";

export const toolDefinitionSchema = z
  .object({
    name: z.string().regex(/^[a-z][a-z0-9_]*$/u),
    operationId: z.string().min(1),
    enabled: z.boolean().default(false),
    title: z.string().min(3),
    description: z.string().min(10),
    risk: z.enum(["read", "write", "destructive"]),
  })
  .strict();

export const definitionFileSchema = z
  .object({
    version: z.literal(1),
    group: z.string().regex(/^[a-z][a-z0-9_-]*$/u),
    tools: z.array(toolDefinitionSchema).min(1),
  })
  .strict();

export type ToolDefinition = z.infer<typeof toolDefinitionSchema>;
