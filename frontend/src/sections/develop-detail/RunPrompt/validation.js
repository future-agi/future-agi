import { z } from "zod";
import {
  AudioContent,
  ImageContent,
  MODEL_TYPES,
  PdfContent,
  TextContent,
} from "./common";

// Define content variants

// Build the schema
const MessageValidationSchema = z
  .object({
    id: z.string(),
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
    if (data.role === "user") {
      const hasValidContent = data.content.some((item) => {
        if (item.type === "text") {
          return item.text.trim().length > 0;
        } else if (item.type === "audio_url") {
          return item.audio_url?.url?.trim() !== "";
        } else if (item.type === "pdf_url") {
          return item.pdf_url?.url?.trim() !== "";
        } else if (item.type === "image_url") {
          return item.image_url?.url?.trim()?.length > 0;
        }
        return false;
      });

      if (!hasValidContent) {
        ctx.addIssue({
          path: ["content"],
          code: z.ZodIssueCode.custom,
          message: "Content is required",
        });
      }
    }
  });

export const RunPromptValidationSchema = (isConditionalNode = false) => {
  return z.object({
    name: isConditionalNode
      ? z.string().transform(() => "test")
      : z.string().min(1, "Name is required"),
    config: z
      .object({
        model: z.string().min(1, "Model is required"),
        run_prompt_config: z
          .object({
            modelName: z.string().optional(),
            logoUrl: z.string().nullable().optional(),
            providers: z.string().optional(),
            isAvailable: z.boolean().optional(),
            voice: z.string().optional(),
            voiceId: z.string().optional(),
          })
          .catchall(z.any()),
        modelType: z.union([z.enum(["llm", "tts", "stt"]), z.string()]),
        voiceInputColumn: z.string().optional(),
        concurrency: z
          .number()
          .positive("Please enter number of chunks to fetch")
          .max(10, "Concurrency cannot be greater than 10"),
        messages: z
          .array(MessageValidationSchema)
          .min(1, "At least one message is required"),
        responseFormat: z.string().nullable(),
        // temperature: z.number().min(0).max(2).nullable(),
        // topP: z.number().min(0).max(2).nullable(),
        // maxTokens: z.number().min(1).max(128000).nullable(),
        // presencePenalty: z.number().min(-2).max(2).nullable(),
        // frequencyPenalty: z.number().min(-2).max(2).nullable(),
        // toolChoice: z.enum(["auto", "required", "none"]),
        tools: z
          .array(z.any())
          .transform((val) => val.map((t) => ({ id: t })))
          .optional(),
        prompt: z.string().optional(),
        promptVersion: z.string().optional(),
      })
      .catchall(z.any())
      .superRefine((data, ctx) => {
        // Form state uses `modelName` (camelCase, matches the schema shape
        // on line 61). Read both casings so legacy snake_case payloads
        // hydrated from the backend also pass — without this, Update
        // submits fail silently with "Model name is required" because
        // `model_name` is always undefined.
        const modelName =
          data?.run_prompt_config?.modelName ??
          data?.run_prompt_config?.model_name;
        if (!modelName || modelName.trim() === "") {
          ctx.addIssue({
            path: ["run_prompt_config"], // << apply error to entire modelDetail object
            code: z.ZodIssueCode.custom,
            message: "Model name is required",
          });
        }

        if (
          data?.run_prompt_config.modelType === MODEL_TYPES.TTS &&
          data?.run_prompt_config?.voice?.trim() === ""
        ) {
          ctx.addIssue({
            path: ["run_prompt_config", "voice"],
            code: z.ZodIssueCode.custom,
            message: "Voice is required",
          });
        }
      }),
    // .superRefine((data, ctx) => {
    //   const toolChoice = data.toolChoice;
    //   if (
    //     (toolChoice === "required" || toolChoice === "auto") &&
    //     data.tools.length === 0
    //   ) {
    //     ctx.addIssue({
    //       code: z.ZodIssueCode.custom,
    //       message:
    //         "At least one tool is required when tool choice is required or auto",
    //       path: ["tools"],
    //     });
    //   }
    // }),
  });
};
