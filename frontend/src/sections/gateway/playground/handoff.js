// Pure helpers of the gateway-to-Prompts handoff (PlaygroundHandoff.jsx).
import { createDraftPayload } from "src/sections/workbench/constant";

const textMessage = (role, text) => ({
  role,
  content: [{ type: "text", text }],
});

/** The Prompts model-list row for a gateway model id, or null. */
export function workbenchModelFor(modelId, rows = []) {
  if (!modelId) return null;
  const exact = rows.find((row) => row.model_name === modelId);
  if (exact) return exact;
  const slash = modelId.indexOf("/");
  if (slash < 0) return null;
  const provider = modelId.slice(0, slash);
  const bare = modelId.slice(slash + 1);
  return (
    rows.find((row) => row.model_name === bare && row.providers === provider) ||
    null
  );
}

export function draftBody(row, { prompt, system }) {
  const messages = [
    textMessage("system", system || ""),
    textMessage("user", prompt || ""),
  ];
  const config = { ...createDraftPayload.prompt_config[0], messages };
  if (row) {
    config.configuration = {
      model: row.model_name,
      model_detail: {
        model_name: row.model_name,
        providers: row.providers,
        logo_url: row.logo_url || "",
        is_available: !!row.is_available,
        type: row.type,
      },
    };
  }
  return { ...createDraftPayload, prompt_config: [config] };
}
