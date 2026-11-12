// WALK-STG-FA-PICKERS-NO-CREDITS-MODELS-WORKBENCH: the server refuses a run
// for a model with no provider key and no credits route with
// "API key not configured for {provider}. Please add your API key in
// settings." (model_route.py NOT_CONFIGURED_MESSAGE). The workbench says
// what works instead, in the shared picker copy, word for word.

export const PROVIDER_KEY_LINK_LABEL = "Settings → AI Providers";
export const GATEWAY_MODELS_LINK_LABEL = "AI Gateway → Models";
export const GATEWAY_KEYS_LINK_LABEL = "AI Gateway → API keys";

export const PROVIDER_KEY_SENTENCE =
  "Run this with your own provider key (Settings → AI Providers).";
export const CREDITS_SENTENCE =
  "Run this with your own provider key (Settings → AI Providers), or try models with your Future AGI credits: open a model in AI Gateway → Models and use Try it.";
// Credits are on, but the gateway does not serve Try it here
// (delegated_playground_available false): an API key on the gateway instead.
export const API_KEY_SENTENCE =
  "Run this with your own provider key (Settings → AI Providers), or call the gateway with an API key on your Future AGI credits (AI Gateway → API keys).";

const REFUSAL = /API key not configured for /i;
const BARE_ASK = /\s*Please add your API key in settings\.?\s*$/i;

export const isProviderKeyRefusal = (text) =>
  typeof text === "string" && REFUSAL.test(text);

// The server's reason without its bare "add your key" ask.
export const refusalLead = (text) => String(text ?? "").replace(BARE_ASK, "");

// Credits are on: the AI Gateway's managed-models status says this
// deployment offers them and the organization has them on. Loading, a
// failed read or any other answer counts as off.
export const creditsOnFromStatus = (status) =>
  status?.isError !== true &&
  status?.data?.available === true &&
  status?.data?.enabled === true;

// A Jinja template that reads an attribute the sandbox does not allow is
// refused before the model is called, with one plain line
// (model_hub/utils/prompt_rendering.py _refused_message). The stream sends it
// in an error frame; the HTTP run stores it as the row's output, so the page
// knows it by its words, as it knows the provider-key refusal above.
const TEMPLATE_REFUSAL =
  /^Not run: (the prompt reads [\s\S]+, which a template is not allowed to read|part of the prompt's template is not allowed)\. Remove it from the prompt and run again\.$/;

export const isTemplateRefusal = (text) =>
  typeof text === "string" && TEMPLATE_REFUSAL.test(text);

// A toast cannot read the credits status, so it names the provider key
// route only. Other messages pass through unchanged.
export const runRefusalToast = (text) =>
  isProviderKeyRefusal(text)
    ? `${refusalLead(text)} ${PROVIDER_KEY_SENTENCE}`
    : text;
