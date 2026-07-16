import { getRandomId } from "src/utils/utils";
import {
  normalizeResponseFormat,
  resolveResponseSchema,
} from "../AgentBuilder/NodeDrawer/nodeFormUtils";

// Agent builder has no attachment upload and can't surface media, so drop
// non-text content items when importing a workbench prompt into a node.
function toTextOnlyContent(content) {
  if (!Array.isArray(content)) return content;
  const textOnly = content.filter((block) => block?.type === "text");
  return textOnly.length ? textOnly : [{ type: "text", text: "" }];
}

/**
 * Maps a version's promptConfigSnapshot into a form-compatible config shape.
 * Used when importing a prompt and when changing versions in the dropdown.
 *
 * @param {Object} version - A version object from the prompt-versions API
 * @returns {Object} Config compatible with getDefaultValues / setValue
 */
export function mapVersionToFormConfig(version) {
  const snapshot = version?.prompt_config_snapshot;
  const cfg = snapshot?.configuration || {};

  const templateFormat =
    cfg.template_format || snapshot?.template_format || "mustache";

  return {
    outputFormat: cfg.output_format || snapshot?.output_format || "string",
    templateFormat,
    modelConfig: {
      model: cfg.model || "",
      modelDetail: cfg.model_detail || {},
      toolChoice: cfg.tool_choice || "auto",
      tools: cfg.tools || [],
      responseFormat: normalizeResponseFormat(cfg.response_format),
      responseSchema: resolveResponseSchema(
        cfg.response_format,
        cfg.response_schema,
      ),
    },
    messages: (() => {
      const msgs = (snapshot?.messages || []).map((m) => ({
        id: getRandomId(),
        role: m.role,
        content: toTextOnlyContent(m?.content),
      }));
      if (!msgs.some((m) => m.role === "system")) {
        msgs.unshift({
          id: getRandomId(),
          role: "system",
          content: [{ type: "text", text: "" }],
        });
      }
      if (!msgs.some((m) => m.role === "user")) {
        msgs.push({
          id: getRandomId(),
          role: "user",
          content: [{ type: "text", text: "" }],
        });
      }
      return msgs;
    })(),
    payload: {
      promptConfig: [
        {
          configuration: {
            temperature: cfg.temperature,
            maxTokens: cfg.max_tokens,
            topP: cfg.top_p,
            frequencyPenalty: cfg.frequency_penalty,
            presencePenalty: cfg.presence_penalty,
            ...(cfg.response_schema && {
              response_schema: cfg.response_schema,
            }),
            template_format: templateFormat,
            ...(cfg.reasoning && { reasoning: cfg.reasoning }),
          },
        },
      ],
    },
  };
}
