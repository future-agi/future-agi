import { z } from "zod";
import { messageSuperRefineFunction } from "../Common/validation";
import { sanitizeContent } from "src/utils/utils";
import {
  AudioContent,
  ImageContent,
  MODEL_TYPES,
  PdfContent,
  TextContent,
} from "../RunPrompt/common";
import { normalizeForComparison } from "src/sections/workbench/createPrompt/Playground/common";
import { isUUID } from "src/utils/utils";
import { promptConfigTransform } from "./utils";
import { targetTypeOf } from "./runPlan";

const getMessageValidationSchema = () =>
  z
    .object({
      id: z.string().optional(),
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
      // Variable validation is handled separately in PromptTemplateCard
      // to support JSON column dot notation which requires async data loading

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
            message: "User prompt is required",
          });
        }
      }
    });

const createMessageTransform = (allColumns) => (msgs) =>
  msgs.map(({ id, ...rest }) => {
    let content = rest.content;
    if (Array.isArray(content)) {
      content = content.map((part) => {
        if (part.type === "text" && part.text) {
          let updatedText = sanitizeContent(part.text);

          const variablePattern = /{{\s*([^{}]+?)\s*}}/g;
          const matches = [...updatedText.matchAll(variablePattern)];

          matches.forEach((match) => {
            const rawVar = match[1].trim();

            const column = allColumns.find(
              ({ headerName }) =>
                normalizeForComparison(headerName).toLowerCase() ===
                normalizeForComparison(rawVar).toLowerCase(),
            );

            if (column) {
              const replacePattern = new RegExp(
                `{{\\s*${rawVar.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*}}`,
                "g",
              );
              updatedText = updatedText.replace(
                replacePattern,
                `{{${column.field}}}`,
              );
            } else {
              const dotIndex = rawVar.indexOf(".");
              if (dotIndex > 0) {
                const baseColumn = rawVar.substring(0, dotIndex);
                const jsonPath = rawVar.substring(dotIndex + 1);
                const jsonColumn = allColumns.find(
                  ({ headerName }) =>
                    normalizeForComparison(headerName).toLowerCase() ===
                    normalizeForComparison(baseColumn).toLowerCase(),
                );

                if (jsonColumn?.dataType === "json") {
                  const replacePattern = new RegExp(
                    `{{\\s*${rawVar.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*}}`,
                    "g",
                  );
                  updatedText = updatedText.replace(
                    replacePattern,
                    `{{${jsonColumn.field}.${jsonPath}}}`,
                  );
                }
              }
            }
          });

          return { ...part, text: updatedText };
        }
        return part;
      });
    }
    return { ...rest, content };
  });

const modelSchema = z.object({
  id: z.string().optional(),
  value: z.string().nullable().optional(),
  logoUrl: z.string().optional(),
  providers: z.string().optional(),
  // Edit mode: the run this model row already is (PUT keeps or re-runs it).
  runId: z.string().optional(),
  voices: z
    .array(
      z.object({
        voice: z.string(),
        promptConfigId: z.string().nullable().optional(),
      }),
    )
    .optional(),
});

const modelArraySchema = z
  .array(modelSchema)
  .min(1, "At least one model is required")
  .superRefine((models, ctx) => {
    if (models.some((m) => m.value === "" || m.value === null)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "At least one model is required",
      });
    }
  });

export const getPromptConfigValidation = (allColumns) =>
  z
    .array(
      z.object({
        id: z.string(),
        name: z.string().min(1, { message: "Name is required" }),
        version: z.string().optional(),
        experimentType: z.union([
          z.enum(["llm", "tts", "stt", "image"]),
          z.string(),
        ]),
        messages: z
          .array(getMessageValidationSchema())
          .min(1, { message: "At least one message is required" })
          .superRefine(messageSuperRefineFunction)
          .transform(createMessageTransform(allColumns)),
        model: z
          .array(
            z.object({
              id: z.string().optional(),
              value: z.string(),
              logoUrl: z.string().optional(),
              providers: z.string().optional(),
              voices: z
                .array(
                  z.object({
                    voice: z.string(),
                    promptConfigId: z.string().nullable().optional(),
                  }),
                )
                .optional(),
            }),
          )
          .min(1, "At least one model is required"),
        voiceInputColumnIdId: z.string().optional(),
        voice: z
          .array(
            z.object({
              model: z.string().min(1, "Voice model is required"),
              voices: z
                .array(z.string().min(1))
                .min(1, "At least one voice must be selected"),
            }),
          )
          .optional(),
        modelParams: z.record(z.any()).nullable(),
        configuration: z.object({
          toolChoice: z.string().nullable(),
          tools: z
            .array(z.any())
            .transform((val) => val.map((t) => t?.tool?.value))
            .optional(),
        }),
      }),
    )
    .superRefine((data, ctx) => {
      data.forEach((item, index) => {
        if (item.experimentType === MODEL_TYPES.TTS) {
          if (!item.voice?.length) {
            ctx.addIssue({
              path: [index, "voice"],
              code: z.ZodIssueCode.custom,
              message: "Voice is required",
            });
            return;
          }
          item.voice.forEach((v, vIndex) => {
            if (!v.model?.trim()) {
              ctx.addIssue({
                path: [index, "voice", vIndex, "model"],
                code: z.ZodIssueCode.custom,
                message: "Voice model is required",
              });
            }
            if (!v.voices?.length) {
              ctx.addIssue({
                path: [index, "voice", vIndex, "voices"],
                code: z.ZodIssueCode.custom,
                message: "At least one voice must be selected",
              });
            }
          });
        }
        if (
          item.experimentType === MODEL_TYPES.STT &&
          !item.voiceInputColumnIdId
        ) {
          ctx.addIssue({
            path: [index, "voiceInputColumnIdId"],
            code: z.ZodIssueCode.custom,
            message: "Voice input is required",
          });
        }
      });
    })
    .transform((arr) => arr.map(({ id, ...rest }) => rest));

export const getNewExperimentValidationSchema = (
  allColumns,
  isEditing = false,
  _dataset = null,
) =>
  z
    .object({
      columnId: z
        .string()
        .optional()
        .nullable()
        .default(null)
        .transform((val) => (val === "" ? null : val)),
      name: z
        .string()
        .min(1, { message: "Name is required" })
        .refine((name) => name.trim().length > 0, {
          message: "Name is required",
        }),
      description: z.string().optional().default(""),
      datasetVersionId: z.string().optional().nullable(),
      rowIds: z.array(z.string()).optional().default([]),
      splitIds: z.array(z.string()).optional().default([]),
      trials: z.coerce.number().int().min(1).max(10).optional().default(1),
      maxConcurrency: z.coerce
        .number()
        .int()
        .min(1)
        .max(50)
        .optional()
        .default(10),
      metadata: z
        .array(z.object({ key: z.string(), value: z.any() }))
        .optional()
        .default([]),
      experimentType: z
        .enum(["llm", "tts", "stt", "image"], {
          errorMap: () => ({ message: "Model Type is required" }),
        })
        .default("llm"),
      outputFormat: z
        .enum(["string", "json", "audio", "image"])
        .optional()
        .default("string"),
      promptConfig: z
        .array(
          z.discriminatedUnion("experimentType", [
            z.object({
              experimentType: z.literal("llm"),
              promptId: z.string().optional().nullable(),
              promptVersion: z.string().optional().nullable(),
              agentId: z.string().optional().nullable(),
              agentVersion: z.string().optional().nullable(),
              promptConfigId: z.string().optional().nullable(),
              model: z.array(modelSchema).optional().default([]),
              modelParams: z.record(z.any()).nullable().optional(),
              configuration: z
                .object({
                  toolChoice: z.string().default("auto"),
                  tools: z.array(z.any()),
                })
                .optional(),
              outputFormat: z.enum(["string", "json"]).optional(),
              // Mirrored from the selected prompt version so the array-level
              // superRefine can block Next when a draft version is picked
              // and show the version label in the card-level error (TH-4334).
              isDraft: z.boolean().optional().default(false),
              versionLabel: z.string().optional(),
              name: z.string().optional(),
              // Run target (PRODUCT-SPEC §3.8, R2-SPEC P3): prompt | agent |
              // external | http | code | traces | harness.
              targetType: z
                .enum([
                  "prompt",
                  "agent",
                  "external",
                  "http",
                  "code",
                  "traces",
                  "harness",
                  "voice",
                  // options-truth L1 G5: Already running › A2A / MCP tool.
                  "a2a",
                  "mcp_tool",
                ])
                .optional(),
              // R4 run-source cards (lane Ra): the Level 3 draft, passed through.
              r4: z.record(z.any()).optional(),
              wantedVersion: z.any().optional(),
              // R2 cards: permissive pass-through (R2 manifest row 42c).
              code: z.record(z.any()).optional(),
              traces: z.record(z.any()).optional(),
              harness_config: z.record(z.any()).optional(),
              // options-truth L1 G5: A2A agent / MCP tool cards, passed through.
              a2a: z.record(z.any()).optional(),
              mcp_tool: z.record(z.any()).optional(),
              runId: z.string().optional(),
              runName: z.string().optional(),
              http: z
                .object({
                  url: z.string().optional(),
                  method: z.string().optional(),
                  bodyTemplate: z.string().optional(),
                  outputPath: z.string().optional(),
                  // options-truth L1 G5: headers by secret, streaming, audio.
                  headers: z.array(z.any()).optional(),
                  streaming: z.boolean().optional(),
                  audio: z.any().optional(),
                  // W6 B2: the response transform and header preset survive
                  // the form (RunSettings/cardExtras/endpoint.js sends them).
                  transform: z.any().optional(),
                  preset: z.any().optional(),
                })
                .optional(),
              // W6 B5: a variable retrieved at run time (contextSourceModel).
              contextSource: z.any().optional(),
              // Variables are mapped on the Data step (stepIssues.dataIssues).
              variables: z.array(z.string()).optional().default([]),
              // IT-R4 G1 P1: a column id, `{kind: "empty"}` or
              // `{kind: "constant", value}`.
              variableMapping: z
                .record(
                  z.union([
                    z.string(),
                    z.object({
                      kind: z.enum(["empty", "constant"]),
                      value: z.string().optional(),
                    }),
                  ]),
                )
                .optional()
                .default({}),
              unmappedVariables: z.number().optional().default(0),
            }),
            z.object({
              experimentType: z.literal("tts"),
              messages: z
                .array(getMessageValidationSchema())
                .min(1, { message: "At least one message is required" })
                .superRefine(messageSuperRefineFunction)
                .transform(createMessageTransform(allColumns)),
              model: modelArraySchema,
              modelParams: z.record(z.any()).nullable(),
              configuration: z.object({
                toolChoice: z.string(),
                tools: z.array(z.any()),
              }),
            }),
            z.object({
              experimentType: z.literal("stt"),
              messages: z
                .array(getMessageValidationSchema())
                .min(1, { message: "At least one message is required" })
                .superRefine(messageSuperRefineFunction)
                .transform(createMessageTransform(allColumns)),
              model: modelArraySchema,
              voiceInputColumnId: z
                .string()
                .uuid({ message: "Voice input is required" }),
              modelParams: z.record(z.any()),
              configuration: z.object({
                toolChoice: z.string().default("auto"),
                tools: z.array(z.any()),
              }),
            }),
            z.object({
              experimentType: z.literal("image"),
              messages: z
                .array(getMessageValidationSchema())
                .min(1, { message: "At least one message is required" })
                .superRefine(messageSuperRefineFunction)
                .transform(createMessageTransform(allColumns)),
              model: modelArraySchema,
              modelParams: z.record(z.any()),
            }),
          ]),
        )
        .min(1, { message: "Add at least one run" })
        .superRefine((items, ctx) => {
          items.forEach((item, index) => {
            if (item.experimentType !== "llm") return;

            // Model check only applies to prompt runs; agents, From code
            // and HTTP runs pick no model here.
            if (targetTypeOf(item) === "prompt") {
              if (
                item.model.length === 0 ||
                item.model.some((m) => m.value === "" || m.value === null)
              ) {
                ctx.addIssue({
                  code: z.ZodIssueCode.custom,
                  message: "At least one model is required",
                  path: [index, "model"],
                });
              }
            }

            // Block advancing when the selected version is still a
            // draft. Applies to both prompt rows (TH-4334) and agent
            // rows (TH-4355). Per-item issue so the error renders
            // inline inside the specific card (AgentPromptRenderer
            // reads `errors.promptConfig.[index].isDraft.message`).
            // Version label is mirrored from the selected version so
            // the user knows which one needs saving/swapping.
            if (item.isDraft === true) {
              const kind = item.agentId ? "Agent" : "Prompt";
              const versionTag = item.versionLabel
                ? ` ${item.versionLabel}`
                : "";
              ctx.addIssue({
                code: z.ZodIssueCode.custom,
                message: `${kind} version${versionTag} is in draft. Save this version, or pick a saved version, to run the experiment.`,
                path: [index, "isDraft"],
              });
            }
          });
        }),
      userEvalMetrics: z
        .array(z.any())
        .min(1, { message: "Add at least one evaluation" }),
    })
    .superRefine((data, ctx) => {
      const allowedFormats = {
        llm: ["string", "json"],
        stt: ["string", "json"],
        tts: ["audio"],
        image: ["image"],
      };
      const allowed = allowedFormats[data.experimentType];
      if (
        allowed &&
        data.outputFormat &&
        !allowed.includes(data.outputFormat)
      ) {
        ctx.addIssue({
          path: ["outputFormat"],
          code: z.ZodIssueCode.custom,
          message: `Output format must be ${allowed.join(" or ")} for ${data.experimentType} experiments`,
        });
      }
    })
    .transform((data) => ({
      ...data,
      promptConfig: promptConfigTransform(
        data.promptConfig,
        data.experimentType,
        data.outputFormat,
        isEditing,
      ),
      userEvalMetrics: (data.userEvalMetrics ?? []).map((evalItem) => ({
        ...(isEditing &&
          evalItem?.actualEvalCreatedId &&
          isUUID(evalItem?.actualEvalCreatedId) && {
            id: evalItem?.actualEvalCreatedId,
          }),
        template_id:
          evalItem?.templateDetails?.id ||
          evalItem.templateId ||
          evalItem.template_id ||
          evalItem.id,
        name:
          evalItem.name || evalItem.evalTemplateName || "Unnamed Evaluation",
        config: evalItem.config,
        // `model` is optional on the wire and not nullable (EvalMetricEntry).
        ...(evalItem.model ? { model: evalItem.model } : {}),
        error_localizer: evalItem.errorLocalizer ?? evalItem.error_localizer,
        kb_id: evalItem.kbId || evalItem.kb_id || null,
      })),
    }));

export const getExperimentValidationSchema = (allColumns) =>
  z.object({
    columnId: z.string().min(1, { message: "Column is required" }),
    name: z.string().min(1, { message: "Name is required" }),
    outputFormat: z.enum(["string", "array", "number", "object", "audio"]),
    promptConfig: getPromptConfigValidation(allColumns),
    userEvalMetrics: z
      .array(z.any())
      .optional()
      .transform((v) => v?.map((v) => v.id)),
  });

export const getParsedPromptConfigSchema = (experimentType) =>
  z
    .array(z.any())
    .transform((data) => promptConfigTransform(data, experimentType));
