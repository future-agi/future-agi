export const LOGO_WITH_BLACK_BACKGROUND = ["openai", "cerebras", "ollama"];

// model_detail objects exist in two shapes: the backend contract is snake_case
// (models_list, prompt template snapshots) while dropdown-picked values carry
// camelCase aliases. Fill both so every reader resolves regardless of source.
export const normalizeModelOption = (option) => {
  if (!option || option?.value === "no") return option;
  const modelName =
    option.modelName ?? option.model_name ?? option.name ?? option.value ?? "";
  const type = option.type ?? option.mode ?? option.model_type;
  return {
    ...option,
    modelName,
    model_name: option.model_name ?? modelName,
    providers: option.providers ?? option.provider ?? "",
    isAvailable: option.isAvailable ?? option.is_available ?? false,
    is_available: option.is_available ?? option.isAvailable ?? false,
    logoUrl: option.logoUrl ?? option.logo_url ?? "",
    logo_url: option.logo_url ?? option.logoUrl ?? "",
    // Never fabricate `type`: the backend's modality filter treats a MISSING
    // model_detail.type as "chat", but an empty string matches nothing —
    // stamping "" here would drop resaved prompts from modality tabs.
    ...(type !== undefined && { type }),
  };
};
