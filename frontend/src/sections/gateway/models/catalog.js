// Catalog vocabulary and the pure filtering, sorting and formatting the
// Models page runs over the managed catalog. Prices are USD per 1M tokens.

export const CAPABILITIES = [
  {
    key: "tools",
    label: "Tools",
    description: "Function and tool calling",
    icon: "mdi:wrench-outline",
    test: (m) => hasParam(m, "tools"),
  },
  {
    key: "structured",
    label: "Structured output",
    description: "JSON schema or JSON mode responses",
    icon: "mdi:code-json",
    test: (m) =>
      hasParam(m, "structured_outputs") || hasParam(m, "response_format"),
  },
  {
    key: "vision",
    label: "Image input",
    description: "Accepts images in messages",
    icon: "mdi:image-outline",
    test: (m) => listOf(m?.input_modalities).includes("image"),
  },
  {
    key: "reasoning",
    label: "Reasoning",
    description: "Exposes a reasoning or thinking budget",
    icon: "mdi:head-lightbulb-outline",
    test: (m) => hasParam(m, "reasoning") || hasParam(m, "include_reasoning"),
  },
];

export const CONTEXT_OPTIONS = [
  { value: 0, label: "Any context" },
  { value: 32000, label: "32K or more" },
  { value: 128000, label: "128K or more" },
  { value: 200000, label: "200K or more" },
  { value: 1000000, label: "1M or more" },
];

export const SORT_OPTIONS = [
  { value: "newest", label: "Newest" },
  { value: "price_asc", label: "Price: low to high" },
  { value: "price_desc", label: "Price: high to low" },
  { value: "context_desc", label: "Context: largest" },
];

export const DEFAULT_FILTERS = {
  search: "",
  vendor: "",
  capabilities: [],
  minContext: 0,
  sort: "newest",
};

const VENDOR_LABELS = {
  ai21: "AI21",
  amazon: "Amazon",
  anthropic: "Anthropic",
  cohere: "Cohere",
  deepseek: "DeepSeek",
  google: "Google",
  "meta-llama": "Meta",
  microsoft: "Microsoft",
  minimax: "MiniMax",
  mistralai: "Mistral",
  moonshotai: "Moonshot AI",
  nousresearch: "Nous Research",
  nvidia: "NVIDIA",
  openai: "OpenAI",
  perplexity: "Perplexity",
  qwen: "Qwen",
  "x-ai": "xAI",
  "z-ai": "Z.ai",
};

function listOf(value) {
  return Array.isArray(value) ? value : [];
}

function hasParam(model, name) {
  return listOf(model?.supported_parameters).includes(name);
}

export function vendorLabel(slug) {
  if (!slug) return "Other";
  if (VENDOR_LABELS[slug]) return VENDOR_LABELS[slug];
  return slug
    .split(/[-_]/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

// Catalog names read "Anthropic: Claude Sonnet 5.5"; the vendor has its own
// column, so the prefix is dropped.
export function modelDisplayName(model) {
  const name = String(model?.name || model?.id || "");
  const idx = name.indexOf(": ");
  return idx > 0 ? name.slice(idx + 2) : name;
}

export function modelCapabilities(model) {
  return CAPABILITIES.filter((c) => c.test(model)).map((c) => c.key);
}

// Dynamic-priced models are charged per request by the provider, so they have
// no list price to show or sort by.
export function hasListPrice(model) {
  return (
    !model?.dynamic_price &&
    Number.isFinite(model?.billed_input_per_mtok) &&
    Number.isFinite(model?.billed_output_per_mtok)
  );
}

// Models with no vendor are grouped under one filterable value, since "" is
// the filter's "all vendors".
const NO_VENDOR = "__none__";
const vendorKey = (model) => model?.vendor || NO_VENDOR;

export function vendorOptions(models) {
  const counts = new Map();
  listOf(models).forEach((m) => {
    const v = vendorKey(m);
    counts.set(v, (counts.get(v) || 0) + 1);
  });
  return Array.from(counts, ([value, count]) => ({
    value,
    label: vendorLabel(value === NO_VENDOR ? "" : value),
    count,
  })).sort((a, b) => a.label.localeCompare(b.label));
}

export function filterModels(models, filters = DEFAULT_FILTERS) {
  const terms = String(filters.search || "")
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean);
  const caps = CAPABILITIES.filter((c) =>
    listOf(filters.capabilities).includes(c.key),
  );
  const minContext = Number(filters.minContext) || 0;

  return listOf(models).filter((m) => {
    if (filters.vendor && vendorKey(m) !== filters.vendor) return false;
    if (minContext && !((m.context_length || 0) >= minContext)) return false;
    if (caps.some((c) => !c.test(m))) return false;
    if (terms.length) {
      const haystack = [m.id, m.name, m.vendor, vendorLabel(m.vendor)]
        .join(" ")
        .toLowerCase();
      if (!terms.every((t) => haystack.includes(t))) return false;
    }
    return true;
  });
}

const byName = (a, b) => modelDisplayName(a).localeCompare(modelDisplayName(b));

// Price sorts keep unpriced models last in either direction: "varies" is not
// cheaper or dearer than a list price.
function comparePrice(a, b, direction) {
  const pa = hasListPrice(a);
  const pb = hasListPrice(b);
  if (pa !== pb) return pa ? -1 : 1;
  if (!pa) return byName(a, b);
  const diff =
    a.billed_input_per_mtok - b.billed_input_per_mtok ||
    a.billed_output_per_mtok - b.billed_output_per_mtok;
  return diff ? diff * direction : byName(a, b);
}

export function sortModels(models, sort = "newest") {
  const list = [...listOf(models)];
  switch (sort) {
    case "price_asc":
      return list.sort((a, b) => comparePrice(a, b, 1));
    case "price_desc":
      return list.sort((a, b) => comparePrice(a, b, -1));
    case "context_desc":
      return list.sort(
        (a, b) =>
          (b.context_length || 0) - (a.context_length || 0) || byName(a, b),
      );
    default:
      return list.sort(
        (a, b) => (b.created || 0) - (a.created || 0) || byName(a, b),
      );
  }
}

// 1048576 -> "1.05M", 200000 -> "200K".
export function formatTokenCount(value) {
  const n = Number(value);
  if (!value || !Number.isFinite(n) || n <= 0) return "—";
  if (n >= 1_000_000) return `${trimZeros((n / 1_000_000).toFixed(2))}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}K`;
  return String(n);
}

function trimZeros(text) {
  return text.includes(".") ? text.replace(/\.?0+$/, "") : text;
}

// $3.00, $3.15, $0.2625, $0.40 per 1M tokens: cents at least, so a price
// column lines up.
export function formatPerMillion(value) {
  if (value == null || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  if (n === 0) return "Free";
  if (n < 0.0001) return "<$0.0001";
  if (n >= 1) return `$${n.toFixed(2)}`;
  const fixed = n.toFixed(4).replace(/0+$/, "");
  const [whole, decimals = ""] = fixed.split(".");
  return `$${whole}.${decimals.padEnd(2, "0")}`;
}

export function formatPercent(value) {
  const n = Number(value);
  if (value == null || !Number.isFinite(n)) return "—";
  return `${trimZeros(n.toFixed(2))}%`;
}
