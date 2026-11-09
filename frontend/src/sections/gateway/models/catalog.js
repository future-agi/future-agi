import { formatExactMoney } from "src/sections/common/format/money";
// Catalog vocabulary and the pure filtering, sorting and formatting the
// Models page runs over the managed catalog. Legacy token prices are USD per 1M.
import { hasInclusivePrice } from "./inclusivePricing";

import { fToNow } from "src/utils/format-time";

import { formatDateTime } from "../utils/formatters";

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

// Words a vendor slug spells in lower case that read upper case in a name.
const ACRONYMS = new Set(["ai", "agi", "llm", "ml", "nlp", "api"]);

/**
 * The vendor as the catalog files it, without the leading "~" the catalog
 * gives its alias entries ("~perplexity" is Perplexity).
 */
export function vendorSlug(slug) {
  return String(slug || "").replace(/^~+/, "");
}

export function vendorLabel(slug) {
  const key = vendorSlug(slug);
  if (!key) return "Other";
  if (VENDOR_LABELS[key]) return VENDOR_LABELS[key];
  return key
    .split(/[-_]/)
    .filter(Boolean)
    .map((part) =>
      ACRONYMS.has(part.toLowerCase())
        ? part.toUpperCase()
        : part.charAt(0).toUpperCase() + part.slice(1),
    )
    .join(" ");
}

// Catalog sources whose prices the gateway fetched (a restored list is the
// last fetched one, read back at boot); the others are built in.
const FETCHED_SOURCES = new Set(["live", "restored"]);
const BUILT_IN = "the price list built into this gateway release";

/**
 * Where the catalog's prices come from, judged per provider: { text, tip,
 * bundled }, or null with no providers. One provider on the built-in list
 * does not mark fetched prices as built in.
 */
export function catalogFreshness(providers) {
  const list = listOf(providers);
  if (!list.length) return null;
  const fetched = list.filter((p) => FETCHED_SOURCES.has(p.source));
  const latest = (items) =>
    items
      .map((p) => p.fetched_at)
      .filter(Boolean)
      .sort()
      .pop() || null;
  const describe = (p) => {
    const when = p.fetched_at ? formatDateTime(p.fetched_at) : "";
    const name = p.provider || "A provider";
    if (p.source === "bundled") {
      return `${name}: ${BUILT_IN}${when ? `, from ${when}` : ""}.`;
    }
    if (p.source === "price_card") {
      return `${name}: the gateway's price card${when ? `, read ${when}` : ""}.`;
    }
    return `${name}: fetched ${when || "recently"}.`;
  };
  if (fetched.length) {
    const at = latest(fetched);
    const rest = list.filter((p) => !FETCHED_SOURCES.has(p.source));
    return {
      bundled: false,
      text: `Prices refreshed ${at ? fToNow(at) : "recently"}`,
      tip: [
        at ? `Catalog fetched ${formatDateTime(at)}.` : "",
        ...rest.map(describe),
      ]
        .filter(Boolean)
        .join("\n"),
    };
  }
  if (list.every((p) => p.source === "bundled")) {
    const at = latest(list);
    return {
      bundled: true,
      text: "Bundled price list",
      tip: `The gateway has not fetched live prices, so it uses ${BUILT_IN}${at ? `, from ${formatDateTime(at)}` : ""}.`,
    };
  }
  return {
    bundled: false,
    text: "Price card",
    tip: list.map(describe).join("\n"),
  };
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

// Availability is the gateway's shared admission decision, represented by
// the catalogue state. Price flags also keep older catalogue replies safe.
export function availableOnCredits(model) {
  return (
    Boolean(model) &&
    model.state !== "blocked" &&
    (!model.dynamic_price || hasInclusivePrice(model)) &&
    (!model.inclusive_price || hasInclusivePrice(model)) &&
    !model.unpriced &&
    !model.stale_price
  );
}

export function hasOnlyRequestPrice(model) {
  const prices = model?.prices;
  return (
    prices?.request > 0 &&
    Object.entries(prices).every(
      ([line, price]) => line === "request" || price === 0,
    )
  );
}

// Dynamic prices have no fixed list price to show or sort by.
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
  const exact = formatExactMoney(value);
  return exact === "$0.00" ? "Free" : exact;
}

export function formatPercent(value) {
  const n = Number(value);
  if (value == null || !Number.isFinite(n)) return "—";
  return `${trimZeros(n.toFixed(2))}%`;
}
