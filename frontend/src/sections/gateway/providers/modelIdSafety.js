// Only call for lists returned by the credential-aware server projection.
// This adapter does not decide admission and never coerces or rewrites an ID.
// In particular, provider.config.models from legacy gateway reads is NOT an
// input: the dialog loads saved_models_only before rendering saved chips.
export const serverModelIds = (values) =>
  Array.isArray(values)
    ? values.filter((value) => typeof value === "string" && value.length > 0)
    : [];

export const droppedModelCount = (value) =>
  Number.isSafeInteger(value) && value > 0 ? value : 0;
