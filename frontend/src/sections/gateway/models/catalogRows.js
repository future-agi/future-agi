// The Models catalog as rows: what each catalog model is (its type and the
// gateway route that serves it), how this organization can reach it (managed
// credit, its own provider key, both, or not at all, with the reason), and
// the price it pays. Prices are USD per 1M tokens.
//
// Managed access follows the gateway: managed models serve chat completions
// only (server/managed_endpoints.go), and the managed resolver takes a model
// its catalog can bill (internal/catalog/catalog.go Sellable): not unpriced,
// and not on a price card too old to bill by. Only qualified inclusive
// routers can be billed without a fixed quote. Inclusive dimensions and
// request fees are never treated as scalar/free token pricing.

import {
  CAPABILITIES,
  availableOnCredits,
  hasOnlyRequestPrice,
  CONTEXT_OPTIONS,
  modelDisplayName,
  vendorLabel,
  vendorSlug,
} from "./catalog";
import { vendorLogo } from "./vendorLogos";
import { hasInclusivePrice } from "./inclusivePricing";
import { MANAGED_MODE_LABEL, OWN_KEY_LABEL } from "../utils/providerLabel";

export const TYPE_OPTIONS = [
  { value: "chat", label: "Chat", endpoint: "/chat/completions" },
  { value: "images", label: "Image", endpoint: "/images/generations" },
  { value: "embeddings", label: "Embeddings", endpoint: "/embeddings" },
  { value: "audio_speech", label: "Speech", endpoint: "/audio/speech" },
  {
    value: "audio_transcriptions",
    label: "Transcription",
    endpoint: "/audio/transcriptions",
  },
  { value: "video", label: "Video", endpoint: "/videos" },
  { value: "rerank", label: "Rerank", endpoint: "/rerank" },
  { value: "decisions", label: "Decision", endpoint: "/decisions" },
];

const TYPE_BY_KEY = Object.fromEntries(TYPE_OPTIONS.map((t) => [t.value, t]));

// The catalog's output modality names for each non-chat endpoint.
const TYPE_BY_OUTPUT = {
  image: "images",
  embeddings: "embeddings",
  speech: "audio_speech",
  transcription: "audio_transcriptions",
  video: "video",
  rerank: "rerank",
  decisions: "decisions",
};

const NOUN = {
  images: "an image model",
  embeddings: "an embeddings model",
  audio_speech: "a speech model",
  audio_transcriptions: "a transcription model",
  rerank: "a rerank model",
  decisions: "a decision model",
};

export const ACCESS = {
  MANAGED: "managed",
  OWN_KEY: "own_key",
  BOTH: "both",
  NONE: "none",
  UNKNOWN: "unknown",
};

// Access names the billing modes a model can be called on, by their one
// name on every screen (utils/providerLabel.js).
export const ACCESS_OPTIONS = [
  { value: ACCESS.MANAGED, label: MANAGED_MODE_LABEL },
  { value: ACCESS.OWN_KEY, label: OWN_KEY_LABEL },
  { value: ACCESS.NONE, label: "Not callable" },
  { value: ACCESS.UNKNOWN, label: "Access not verified" },
];

export const ACCESS_LABELS = {
  [ACCESS.MANAGED]: MANAGED_MODE_LABEL,
  [ACCESS.OWN_KEY]: OWN_KEY_LABEL,
  [ACCESS.BOTH]: `${MANAGED_MODE_LABEL} + ${OWN_KEY_LABEL.toLowerCase()}`,
  [ACCESS.NONE]: "Not callable",
  [ACCESS.UNKNOWN]: "Access not verified",
};

// Input price per 1M tokens, as paid. "" is any price.
export const PRICE_OPTIONS = [
  { value: "", label: "Any price" },
  { value: "free", label: "Free" },
  { value: "0.5", label: "Under $0.50 / 1M" },
  { value: "2", label: "Under $2 / 1M" },
  { value: "10", label: "Under $10 / 1M" },
];

const listOf = (value) => (Array.isArray(value) ? value : []);
const isNumber = (value) => typeof value === "number" && Number.isFinite(value);

const NOT_REPORTED = "__not_reported__";
export const validMetadataFacet = (value) =>
  typeof value === "string" && (value === NOT_REPORTED || /^[a-zA-Z0-9][a-zA-Z0-9_.:/-]{0,127}$/.test(value));
const METADATA_FACETS = [
  { key: "parameters", field: "supported_parameters", label: "Supported parameters" },
  { key: "inputModalities", field: "input_modalities", label: "Input modalities" },
];
const reportedValues = (model, field) => listOf(model?.[field])
  .filter((value) => value !== NOT_REPORTED && validMetadataFacet(value));
const metadataMatches = (model, field, selected) => {
  const reported = reportedValues(model, field);
  return listOf(selected).every((value) => value === NOT_REPORTED
    ? !reported.length
    : reported.includes(value));
};

// A catalog's endpoint type when it sends one. Without it, a model that takes
// a prompt and answers in text is a chat model, whatever else it outputs (a
// chat model may also return images or audio): the gateway types a catalog
// model as image generation only when it outputs images and no text
// (internal/catalog/modelinfo.go ModelInfo). Only a model that writes no text
// takes its type from what it outputs.
export function modelType(model) {
  if (TYPE_BY_KEY[model?.endpoint_type]) return model.endpoint_type;
  const outputs = listOf(model?.output_modalities);
  if (!outputs.length || outputs.includes("text")) return "chat";
  for (const out of outputs) {
    if (TYPE_BY_OUTPUT[out]) return TYPE_BY_OUTPUT[out];
  }
  return "chat";
}

export const typeLabel = (type) => TYPE_BY_KEY[type]?.label || "Chat";
export const typeEndpoint = (type) =>
  TYPE_BY_KEY[type]?.endpoint || "/chat/completions";

// Whether managed credits can buy the model, and if not, why. `now` (ms) is
// the moment an expiration date is judged at.
export function managedVerdict(model, { now = Date.now() } = {}) {
  const type = modelType(model);
  if (type === "video") {
    return {
      sold: false,
      reason:
        "This gateway does not serve video generation, so the model cannot be called through it.",
    };
  }
  if (type !== "chat") {
    return {
      sold: false,
      reason: `This is ${NOUN[type]}. Future AGI credits pay for chat models only.`,
    };
  }
  if (!availableOnCredits(model)) {
    const reason = model?.unpriced
      ? "unpriced"
      : model?.stale_price
        ? "price out of date"
        : model?.dynamic_price
          ? "priced per request"
          : "blocked";
    return {
      sold: false,
      reason:
        model?.state === "blocked" && model?.state_reason
          ? model.state_reason
          : `Not available on Future AGI credits: ${reason}. Use your own provider key.`,
    };
  }
  // The gateway refuses a model from its expiration date on (410), judged in
  // UTC as the gateway does (routing/eligibility/rules_catalog.go).
  const expires = Date.parse(model?.expiration_date || "");
  if (Number.isFinite(expires) && now >= expires) {
    return {
      sold: false,
      reason: `This model was retired on ${model.expiration_date}, so the gateway refuses it.`,
    };
  }
  if (model?.managed_allowed === false) {
    return {
      sold: false,
      reason: "The gateway does not offer this model on Future AGI credits.",
    };
  }
  if (
    !hasInclusivePrice(model) &&
    (!isNumber(model?.billed_input_per_mtok) ||
      !isNumber(model?.billed_output_per_mtok))
  ) {
    return {
      sold: false,
      reason:
        "The catalog has no fixed price for this model, so Future AGI credits cannot pay for it.",
    };
  }
  return { sold: true, reason: "" };
}

const providerLabel = (p) => p?.display_name || p?.name || p?.id || "";

// Catalog models the org's own providers serve: a provider whose id is a
// catalog vendor ("openai") serves "<vendor>/<model>" for each model it
// lists. A provider with any other id is not matched: the console does not
// guess which catalog model its model names mean.
export function ownKeyRoutes(providers, catalogModels) {
  const ids = new Set(listOf(catalogModels).map((m) => m.id));
  const vendors = new Set(listOf(catalogModels).map((m) => m.vendor));
  const routes = new Map();
  listOf(providers).forEach((p) => {
    const pid = String(p?.id || p?.name || "");
    if (!vendors.has(pid)) return;
    listOf(p.models).forEach((m) => {
      const id = `${pid}/${m}`;
      if (!ids.has(id)) return;
      const list = routes.get(id) || [];
      list.push({
        providerId: pid,
        providerName: providerLabel(p),
        sendAs: m,
        pushed: p.pushed !== false,
        statusReason: p.pushed === false ? String(p.status_reason || "") : "",
      });
      routes.set(id, list);
    });
  });
  return routes;
}

// { deprecated, date } for a model that is deprecated or has a retirement
// date; null otherwise.
export function retirement(model) {
  const fromReason = /^expires:(\d{4}-\d{2}-\d{2})$/.exec(
    String(model?.state_reason || ""),
  )?.[1];
  const date = model?.expiration_date || fromReason || null;
  const deprecated = model?.state === "deprecated";
  if (!deprecated && !date) return null;
  return { deprecated, date };
}

function accessOf(sold, own, providerAccessState) {
  // Retained routes are useful facts, not authorization after a failed read.
  // Managed eligibility is independently known and must not disappear with it.
  if (providerAccessState !== "ready")
    return sold ? ACCESS.MANAGED : ACCESS.UNKNOWN;
  if (sold && own) return ACCESS.BOTH;
  if (sold) return ACCESS.MANAGED;
  if (own) return ACCESS.OWN_KEY;
  return ACCESS.NONE;
}

export function buildCatalogRows(
  models,
  { providers, managedStatus, logos, now, providerAccessState = "ready" } = {},
) {
  const list = listOf(models);
  const own = ownKeyRoutes(providers, list);
  const offered = managedStatus?.available !== false;
  return list.map((m) => {
    const verdict = offered
      ? managedVerdict(m, { now })
      : {
          sold: false,
          reason:
            "This deployment does not offer Future AGI models; add your own provider key to call it.",
        };
    const ownRoutes = own.get(m.id) || [];
    const ownCallable = ownRoutes.some((r) => r.pushed !== false);
    const access = accessOf(
      verdict.sold,
      ownCallable,
      providerAccessState,
    );
    const type = modelType(m);
    const inclusivePricing = verdict.sold && hasInclusivePrice(m);
    return {
      id: m.id,
      model: m,
      name: modelDisplayName(m),
      // "~perplexity" files under Perplexity, one vendor in the filter.
      vendor: vendorSlug(m.vendor),
      vendorName: vendorLabel(m.vendor),
      vendorLogo: vendorLogo(m.vendor, logos),
      type,
      typeLabel: typeLabel(type),
      // Wave-1 detail recovery recognizes a cold unresolved read via NONE
      // plus the provider query's pending/error state. Keep that sentinel;
      // providerAccessState and accessLabel still distinguish it from absence.
      access:
        access === ACCESS.UNKNOWN && !Array.isArray(providers)
          ? ACCESS.NONE
          : access,
      providerAccessState,
      // Facets can keep a previously matched route visible during a failed
      // refresh, while the label and usable-first sort reflect unknown access.
      lastKnownAccess: Array.isArray(providers)
        ? accessOf(verdict.sold, ownCallable, "ready")
        : undefined,
      offered,
      onCredits: verdict.sold,
      accessLabel: ACCESS_LABELS[access],
      accessReason:
        providerAccessState !== "ready"
          ? "Own-provider access is not verified. Retry the provider read before using an own-key route."
          : !ownCallable && ownRoutes.length
            ? "Your provider is not synced to the gateway. Review it under Providers."
            : verdict.sold
              ? ""
              : verdict.reason,
      managedOn: verdict.sold ? Boolean(managedStatus?.enabled) : false,
      ownRoutes,
      // Inclusive dimensions do not have a scalar token price. Null keeps
      // them out of Free/under-$X facets and price sorting; never use the
      // serializer's legacy zero token fields as a quote for paid generation.
      inclusivePricing,
      input:
        verdict.sold &&
        !inclusivePricing &&
        !hasOnlyRequestPrice(m) &&
        isNumber(m.billed_input_per_mtok)
          ? m.billed_input_per_mtok
          : null,
      output:
        verdict.sold &&
        !inclusivePricing &&
        !hasOnlyRequestPrice(m) &&
        isNumber(m.billed_output_per_mtok)
          ? m.billed_output_per_mtok
          : null,
      requestPrice:
        verdict.sold && !inclusivePricing && hasOnlyRequestPrice(m)
          ? m.prices.request *
            (1 + (Number(managedStatus?.markup_percent) || 0) / 100)
          : null,
      context: isNumber(m.context_length) ? m.context_length : null,
      maxOutput: isNumber(m.max_output_tokens) ? m.max_output_tokens : null,
      released: isNumber(m.created) && m.created > 0 ? m.created : null,
      retirement: retirement(m),
      capabilities: CAPABILITIES.filter((c) => c.test(m)).map((c) => c.key),
    };
  });
}

const reachesManaged = (a) => a === ACCESS.MANAGED || a === ACCESS.BOTH;
const reachesOwnKey = (a) => a === ACCESS.OWN_KEY || a === ACCESS.BOTH;

function priceMatches(row, price) {
  if (!price) return true;
  if (!isNumber(row.input)) return false;
  if (price === "free")
    return row.requestPrice == null && row.input === 0 && row.output === 0;
  return row.input < Number(price);
}

function accessMatches(row, access) {
  if (!access) return true;
  const currentAccess =
    row.access === ACCESS.NONE && (row.providerAccessState ?? "ready") !== "ready"
      ? ACCESS.UNKNOWN
      : row.access;
  if (access === ACCESS.UNKNOWN) return currentAccess === ACCESS.UNKNOWN;
  const knownAccess = row.lastKnownAccess ?? currentAccess;
  if (access === ACCESS.MANAGED) return reachesManaged(knownAccess);
  if (access === ACCESS.OWN_KEY) return reachesOwnKey(knownAccess);
  return knownAccess === access;
}

// A facet's value: any of a list (a checkbox group), or one value.
const anyOf = (value) => (Array.isArray(value) ? value : value ? [value] : []);

/**
 * The rows the filters keep. Type, vendor and access take one value or a
 * list (any of them); price and context one value; capabilities all of a
 * list; search every word.
 */
export function filterRows(rows, filters = {}) {
  const terms = String(filters.search || "")
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean);
  const caps = CAPABILITIES.filter((c) =>
    listOf(filters.capabilities).includes(c.key),
  );
  const minContext = Number(filters.minContext) || 0;
  const types = anyOf(filters.type);
  const vendors = anyOf(filters.vendor);
  const access = anyOf(filters.access);
  return listOf(rows).filter((r) => {
    if (types.length && !types.includes(r.type)) return false;
    if (vendors.length && !vendors.includes(r.vendor)) return false;
    if (access.length && !access.some((a) => accessMatches(r, a))) return false;
    if (!priceMatches(r, filters.price)) return false;
    if (minContext && !((r.context || 0) >= minContext)) return false;
    if (caps.some((c) => !c.test(r.model))) return false;
    if (METADATA_FACETS.some(({ key, field }) => !metadataMatches(r.model, field, filters[key]))) return false;
    if (terms.length) {
      const haystack = [r.id, r.name, r.vendor, r.vendorName, r.typeLabel]
        .join(" ")
        .toLowerCase();
      if (!terms.every((t) => haystack.includes(t))) return false;
    }
    return true;
  });
}

// AG Grid comparator for a price column: rows with no price stay last
// whichever way the column is sorted.
export function comparePaidPrice(a, b, _nodeA, _nodeB, isDescending) {
  const pa = isNumber(a);
  const pb = isNumber(b);
  if (pa && pb) return a - b;
  if (!pa && !pb) return 0;
  const last = isDescending ? -1 : 1;
  return pa ? -last : last;
}

// Same idea for context and other numeric columns.
export const compareNumberLast = comparePaidPrice;

// Filter options with counts, for the Type and Provider selects.
export function countBy(rows, key) {
  const counts = new Map();
  listOf(rows).forEach((r) =>
    counts.set(r[key], (counts.get(r[key]) || 0) + 1),
  );
  return counts;
}

// Catalog descriptions are markdown; the page shows them as plain text.
export function plainDescription(text) {
  return String(text || "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/`([^`]+)`/g, "$1");
}

// ---------------------------------------------------------------------------
// Facets (the catalog's filter panel, OpenRouter-style): each option counts
// the rows it would show with every other facet applied, so the counts of
// one group never shrink as its own options are ticked.
// ---------------------------------------------------------------------------

const countWith = (rows, filters, patch) =>
  filterRows(rows, { ...filters, ...patch }).length;

/**
 * The facet groups for the rows and filters: { key, label, multiple,
 * options: [{ value, label, count, logo? }] }. Type and vendor list only
 * values some row has; the vendors busiest first.
 */
export function catalogFacets(rows, filters = {}) {
  const all = listOf(rows);
  const typeCounts = countBy(all, "type");
  const vendorCounts = countBy(all, "vendor");
  const vendorRow = new Map(all.map((r) => [r.vendor, r]));
  const caps = listOf(filters.capabilities);
  return [
    {
      key: "type",
      label: "Type",
      multiple: true,
      options: TYPE_OPTIONS.filter((t) => typeCounts.get(t.value)).map((t) => ({
        value: t.value,
        label: t.label,
        count: countWith(all, filters, { type: [t.value] }),
      })),
    },
    {
      key: "vendor",
      label: "Provider",
      multiple: true,
      options: [...vendorCounts.keys()]
        .map((v) => ({
          value: v,
          label: vendorRow.get(v)?.vendorName || v,
          logo: vendorRow.get(v)?.vendorLogo || "",
          total: vendorCounts.get(v),
          count: countWith(all, filters, { vendor: [v] }),
        }))
        .sort((a, b) => b.total - a.total || a.label.localeCompare(b.label)),
    },
    {
      key: "access",
      label: "Access",
      multiple: true,
      options: ACCESS_OPTIONS.map((a) => ({
        value: a.value,
        label: a.label,
        count: countWith(all, filters, { access: [a.value] }),
      })),
    },
    {
      key: "price",
      label: "Price",
      multiple: false,
      options: PRICE_OPTIONS.map((p) => ({
        value: p.value,
        label: p.label,
        count: countWith(all, filters, { price: p.value }),
      })),
    },
    {
      key: "minContext",
      label: "Context",
      multiple: false,
      options: CONTEXT_OPTIONS.map((c) => ({
        value: c.value,
        label: c.label,
        count: countWith(all, filters, { minContext: c.value }),
      })),
    },
    ...METADATA_FACETS.map(({ key, field, label }) => {
      const selected = listOf(filters[key]);
      const values = [...new Set([
        ...all.flatMap((row) => reportedValues(row.model, field)),
        ...selected.filter(validMetadataFacet),
      ])].filter((value) => value !== NOT_REPORTED).sort();
      values.push(NOT_REPORTED);
      return {
        key,
        label,
        multiple: true,
        every: true,
        searchable: true,
        description: "Matches all selected facts. Not reported is not a claim of support.",
        options: values.map((value) => ({
          value,
          label: value === NOT_REPORTED ? "Not reported" : value,
          count: countWith(all, filters, {
            [key]: selected.includes(value) ? selected : [...selected, value],
          }),
        })),
      };
    }),
    {
      key: "capabilities",
      label: "Capabilities",
      multiple: true,
      every: true,
      options: CAPABILITIES.map((c) => ({
        value: c.key,
        label: c.label,
        description: c.description,
        count: countWith(all, filters, {
          capabilities: caps.includes(c.key) ? caps : [...caps, c.key],
        }),
      })),
    },
  ];
}
