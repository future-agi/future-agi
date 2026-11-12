import { localProviderLogo } from "src/utils/localProviderLogo";
import { providerDisplayName } from "src/utils/providerDisplayName";
export const editorOptions = {
  selectOnLineNumbers: true,
  roundedSelection: false,
  readOnly: false,
  cursorStyle: "line",
  automaticLayout: true,
  wordWrap: "on",
  lineNumbers: "on",
  folding: true,
  minimap: { enabled: false },
  glyphMargin: false,
  lineDecorationsWidth: 0,
  renderIndentGuides: false,
  lineNumbersMinChars: 0,
  scrollBeyondLastLine: false,
  scrollbar: {
    vertical: "auto",
    horizontal: "visible",
    verticalScrollbarSize: 10,
    horizontalScrollbarSize: 10,
    alwaysConsumeMouseWheel: false,
    useShadows: false,
  },
};

export const CLOUD_ONLY_PROVIDERS = [
  "sagemaker",
  "azure",
  "bedrock",
  "databricks",
];

export const safeParseJson = (value) => {
  try {
    return typeof value === "string" ? JSON.parse(value) : value;
  } catch {
    return { error: "Invalid JSON" };
  }
};

export const validateNonEmptyJsonObject = (value) => {
  try {
    const parsed = JSON.parse(value);
    return (
      typeof parsed === "object" &&
      parsed !== null &&
      !Array.isArray(parsed) &&
      Object.keys(parsed).length > 0
    );
  } catch {
    return false;
  }
};

export const getInitials = (id = "") => {
  const clean = id.replace(/[^a-zA-Z]/g, "");
  return clean.length >= 2
    ? clean.slice(0, 2).toUpperCase()
    : clean.slice(0, 1).toUpperCase();
};

export const buttonStyles = (isSelected, theme) => ({
  color: isSelected ? "primary.dark" : "text.primary",
  backgroundColor: isSelected ? "action.hover" : "transparent",
  border: "1px solid",
  borderColor: isSelected ? "primary.main" : "text.disabled",
  "&:hover": {
    backgroundColor: isSelected ? "action.hover" : theme.palette.action.hover,
  },
});

export const createSortBySelectedProvider = (prioritizedProvider) => {
  return (items) => {
    if (!prioritizedProvider) return items;

    return [...items].sort((a, b) => {
      const aMatch =
        a.provider?.toLowerCase() === prioritizedProvider.toLowerCase();
      const bMatch =
        b.provider?.toLowerCase() === prioritizedProvider.toLowerCase();
      return aMatch === bMatch ? 0 : aMatch ? -1 : 1;
    });
  };
};

// Text as a provider search compares it: lower case, without spaces, "_" or
// "-", so "Hugging Face", "hugging-face" and "huggingface" read the same.
const providerSearchKey = (text) => {
  const lower = String(text ?? "").toLowerCase();
  return lower.replace(/[\s_-]+/g, "");
};

// Whether a provider card matches a search, by the name the card shows or by
// its provider id. Every provider card search uses it.
export const providerMatchesSearch = (provider, search = "") => {
  const term = providerSearchKey(search);
  if (!term) return true;
  const texts = [
    provider?.display_name,
    provider?.displayName,
    provider?.provider,
  ];
  return texts.some((text) => providerSearchKey(text).includes(term));
};

export const filterAndSortProviders = (
  providers = [],
  type,
  search = "",
  sortFn,
) => {
  const filtered = providers.filter((p) => {
    const matchesSearch = providerMatchesSearch(p, search);

    if (type === "json") {
      // Include json-type or cloud-only providers regardless of type
      return (
        (p.type === "json" || CLOUD_ONLY_PROVIDERS.includes(p.provider)) &&
        matchesSearch
      );
    }

    if (type === "text") {
      // Exclude cloud-only providers from default list
      return (
        p.type === "text" &&
        !CLOUD_ONLY_PROVIDERS.includes(p.provider) &&
        matchesSearch
      );
    }

    return false;
  });

  return typeof sortFn === "function" ? sortFn(filtered) : filtered;
};

// A card names its provider with providerDisplayName
// (src/utils/providerDisplayName.js), as a model's missing-key hint does, so
// the card and the hint always read the same: "OpenAI" and "PaLM" where the
// server's list reads "Openai" and "Palm", and the server's own name for a
// provider without a brand entry ("Cohere Chat").
export const normalizeProviderStatus = (provider = {}) => {
  const name = provider.provider ? providerDisplayName(provider.provider) : "";
  return {
    ...provider,
    display_name:
      name ||
      provider.display_name ||
      provider.displayName ||
      provider.provider ||
      "",
    displayName:
      name ||
      provider.displayName ||
      provider.display_name ||
      provider.provider ||
      "",
    hasKey: provider.hasKey ?? provider.has_key ?? false,
    maskedKey: provider.maskedKey ?? provider.masked_key ?? null,
    logoUrl: localProviderLogo(provider),
    logo_url: localProviderLogo(provider),
  };
};

export const getFilterOptions = ({
  defaultModelProviders = [],
  cloudProviders = [],
  filteredCustomModels = [],
}) => {
  const defaultModelCount = defaultModelProviders.length;
  const cloudCount = cloudProviders.length;
  const customModelCount = filteredCustomModels.length;

  const allCount = defaultModelCount + cloudCount + customModelCount;

  return [
    { title: "All", value: "all", icon: "all", count: allCount },
    {
      title: "Default model provider",
      value: "default_model",
      icon: "model",
      count: defaultModelCount,
    },
    {
      title: "Default cloud providers",
      value: "cloud",
      icon: "cloud",
      count: cloudCount,
    },
    {
      title: "Custom model",
      value: "custom",
      icon: "custom",
      count: customModelCount,
    },
  ];
};

export const emptyStateContent = {
  all: {
    title: "No Models Added",
    description: "You haven’t added any models yet.",
  },
  default_model: {
    title: "No Default Models",
    description: "There are no default models available at the moment.",
  },
  cloud: {
    title: "No Cloud Providers",
    description: "No cloud provider integrations have been added yet.",
  },
  custom: {
    title: "No Custom Models",
    description: "Click on + Custom models to add your custom models.",
  },
};
