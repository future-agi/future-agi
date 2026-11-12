// Model-list rows shaped by ONEKEY-ASSUMED-CONTRACT-r1 (modelListField): each
// row carries credential_mode ("managed" | "byok" | "unavailable") and payer
// ("future_agi_credits" | "own_provider_key" | null) next to the legacy
// is_available flag. Pre-contract rows (legacyRow) carry neither field.
//
// This file and ../managedEligibility.js are the only places that spell the
// contract field names; when the real contract lands, both change together.

export const MANAGED_PAYER_LABEL = "Future AGI credits";
export const BYOK_PAYER_LABEL = "Your provider key";
// The provider-key hint names the provider as the product spells it.
export const configureHint = (providerName) =>
  `(Add an API key for ${providerName})`;

export const managedRow = (overrides = {}) => ({
  model_name: "gpt-5.5-mini",
  providers: "openai",
  is_available: true,
  credential_mode: "managed",
  payer: "future_agi_credits",
  type: "llm",
  ...overrides,
});

export const byokRow = (overrides = {}) => ({
  model_name: "claude-sonnet-5",
  providers: "anthropic",
  is_available: true,
  credential_mode: "byok",
  payer: "own_provider_key",
  type: "llm",
  ...overrides,
});

export const unavailableRow = (overrides = {}) => ({
  model_name: "mistral-large-3",
  providers: "mistral",
  is_available: false,
  credential_mode: "unavailable",
  payer: null,
  type: "llm",
  ...overrides,
});

export const legacyRow = (overrides = {}) => ({
  model_name: "gpt-4o",
  providers: "openai",
  is_available: false,
  type: "llm",
  ...overrides,
});
