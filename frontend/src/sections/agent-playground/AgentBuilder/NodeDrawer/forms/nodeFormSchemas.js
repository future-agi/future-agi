import { z } from "zod";
import { NODE_TYPES } from "../../../utils/constants";
import {
  TextContent,
  ImageContent,
  PdfContent,
  AudioContent,
} from "src/sections/develop-detail/RunPrompt/common";

/**
 * Schema for a single message with proper content type validation
 */
const getMessageValidationSchema = () =>
  z
    .object({
      id: z.string().min(1),
      role: z.enum(["user", "assistant", "system"]),
      content: z.array(
        z.discriminatedUnion("type", [
          TextContent,
          ImageContent,
          PdfContent,
          AudioContent,
        ]),
      ),
    })
    .superRefine((data, ctx) => {
      // Validate that user messages have valid content
      if (data.role === "user") {
        const hasValidContent = data.content.some((item) => {
          if (item.type === "text") {
            return item.text.trim().length > 0;
          } else if (item.type === "audio_url") {
            return item.audioUrl?.url?.trim() !== "";
          } else if (item.type === "pdf_url") {
            return item.pdfUrl?.url?.trim() !== "";
          } else if (item.type === "image_url") {
            return item.imageUrl.url.trim().length > 0;
          }
          return false;
        });

        if (!hasValidContent) {
          ctx.addIssue({
            path: ["content"],
            code: z.ZodIssueCode.custom,
            message: "User prompt is required",
          });
        }
      }
    });

/**
 * Schema for model configuration
 */
const modelConfigSchema = z.object({
  model: z.string().min(1, "Please select a model"),
  modelDetail: z
    .object({
      modelName: z.string().optional(),
      logoUrl: z.string().optional(),
      providers: z.string().optional(),
      isAvailable: z.boolean().optional(),
      type: z.string().optional(),
    })
    .optional(),
  responseFormat: z.union([z.string(), z.object({}).passthrough()]).optional(),
  responseSchema: z.union([z.object({}).passthrough(), z.null()]).optional(),
  tools: z.array(z.any()).optional(),
  toolChoice: z.string().optional(),
  maxTokens: z.number().optional(),
});

/**
 * Schema for Prompt Node Form
 * @returns {z.ZodSchema} The Zod schema for prompt node form
 */
export const getPromptNodeFormSchema = () =>
  z.object({
    nodeType: z.literal(NODE_TYPES.LLM_PROMPT).optional(),
    nodeId: z.string().optional(),
    name: z
      .string()
      .min(1, "Prompt name is required")
      .max(255, "Prompt name must be 255 characters or less")
      .regex(
        /^[a-z0-9][a-z0-9_]*$/,
        "Name must be lowercase letters, numbers, and underscores only",
      )
      .trim(),
    // version: z.string().min(1, "Version is required"),
    prompt_version_id: z.string().nullable().optional().default(null),
    prompt_template_id: z.string().nullable().optional().default(null),
    outputFormat: z.string().optional().default("string"),
    templateFormat: z
      .enum(["mustache", "jinja"])
      .optional()
      .default("mustache"),
    modelConfig: modelConfigSchema,
    messages: z
      .array(getMessageValidationSchema())
      .min(1, { message: "At least one message is required" }),
  });

/**
 * Default prompt node form schema
 */
export const promptNodeFormSchema = getPromptNodeFormSchema();

/**
 * Schema for Agent Node Form
 */
export const agentNodeFormSchema = z.object({
  nodeType: z.literal(NODE_TYPES.AGENT).optional(),
  nodeId: z.string().optional(),
  name: z
    .string()
    .min(1, "Node name is required")
    .regex(
      /^[a-z][a-z0-9_]*$/,
      "Name must be lowercase letters, numbers, and underscores only",
    )
    .trim(),
  graphId: z.string().min(1, "Agent is required"),
  versionId: z.string().min(1, "Version is required"),
  inputMappings: z
    .array(
      z.object({ key: z.string(), value: z.string().nullable().optional() }),
    )
    .optional(),
});

export const codeExecutionNodeFormSchema = z.object({
  nodeType: z.literal(NODE_TYPES.CODE_EXECUTION).optional(),
  nodeId: z.string().optional(),
  name: z.string().optional(),
  language: z.enum(["python", "javascript", "typescript"]),
  code: z.string().min(1, "Code is required").max(20000),
  timeout_ms: z.coerce.number().int().min(100).max(30000),
  memory_mb: z.coerce.number().int().min(32).max(512),
});

/**
 * Schema for Eval Node Form
 */
export const evalNodeFormSchema = z.object({
  nodeType: z.literal("eval").optional(),
  nodeId: z.string().optional(),
  name: z
    .string()
    .min(1, "Node name is required")
    .regex(
      /^[a-z][a-z0-9_]*$/,
      "Name must be lowercase letters, numbers, and underscores only",
    )
    .trim(),
  evaluators: z.array(z.any()).optional(),
});

/**
 * Get the appropriate schema based on node type
 * @param {string} nodeType - The type of node ('prompt', 'agent', 'eval')
 * @returns {z.ZodSchema} The Zod schema for the node type
 */
export function getNodeFormSchema(nodeType) {
  switch (nodeType) {
    case NODE_TYPES.LLM_PROMPT:
      return getPromptNodeFormSchema();
    case NODE_TYPES.AGENT:
      return agentNodeFormSchema;
    case NODE_TYPES.CODE_EXECUTION:
      return codeExecutionNodeFormSchema;
    case "eval":
      return evalNodeFormSchema;
    default:
      // Return a basic schema for unknown types
      return z.object({
        nodeType: z.string().optional(),
        nodeId: z.string().optional(),
        name: z
          .string()
          .min(1, "Name is required")
          .regex(
            /^[a-z][a-z0-9_]*$/,
            "Name must be lowercase letters, numbers, and underscores only",
          )
          .trim(),
      });
  }
}
