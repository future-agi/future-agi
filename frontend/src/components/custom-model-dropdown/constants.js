export const MODEL_DROPDOWN_STEPS = {
  MODELS: "models",
  VOICES: "voices",
};

export const MODEL_DROPDOWN_MESSAGES = {
  SEARCH_PLACEHOLDER: "Select model",
  MIN_MODEL_SELECTION_ERROR: "Please select at least one model to continue",
  // {provider}: the display name from src/utils/providerDisplayName.js.
  API_KEY_CONFIG: "Add an API key for {provider}",
};

// What a model menu says when none of its models can run here, and what Run
// prompt says when it refuses a model (CREDITS-PICKER-COPY: the Prompts
// workbench shows the same bytes). The *_LINK parts are links in a menu. The
// credits part shows only when the organization's Future AGI credits are on:
// the Try it path where the gateway serves delegated calls, otherwise a call
// with a gateway API key. Without credits the sentence ends after the
// provider-key part.
export const NO_USABLE_MODEL_COPY = Object.freeze({
  START: "Run this with your own provider key (",
  PROVIDERS_LINK: "Settings → AI Providers",
  PROVIDERS_END: ")",
  END: ".",
  TRY_IT_START:
    ", or try models with your Future AGI credits: open a model in ",
  TRY_IT_LINK: "AI Gateway → Models",
  TRY_IT_END: " and use Try it.",
  API_KEY_START:
    ", or call the gateway with an API key on your Future AGI credits (",
  API_KEY_LINK: "AI Gateway → API keys",
  API_KEY_END: ").",
});

export const MODEL_DROPDOWN_MODES = {
  TOP: "top",
  BOTTOM: "bottom",
};
