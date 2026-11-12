#!/usr/bin/env node
// Test-only source oracle. No app imports, renderer, network, or snapshot update.
// Run ONLY in the guarded frontend kit; stdout is the auditable expectation
// document. Tests call the same pure generator, never learn from product output.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import process from "node:process";

export const ORACLE_NOW = Date.parse("2026-10-29T14:06:39.381Z");
// Use filesystem paths so Vite's asset-URL transform cannot turn fixture
// reads into jsdom's http: URLs. import.meta.url itself stays a file: URL.
const fixtures = dirname(fileURLToPath(import.meta.url));
const read = (name) => JSON.parse(readFileSync(join(fixtures, name), "utf8"));
const sorted = (values) => [...values].sort();
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

export function exactMoney(rate, scale = 1, markup = 0) {
  // Independent rational long division, including scientific JSON numbers.
  const rational = (value) => {
    const match = /^(\d+)(?:\.(\d+))?(?:e([+-]?\d+))?$/i.exec(String(value));
    if (!match) throw new Error(`Invalid oracle decimal: ${value}`);
    const places = (match[2] || "").length - Number(match[3] || 0);
    return [
      BigInt(match[1] + (match[2] || "")) * 10n ** BigInt(Math.max(-places, 0)),
      10n ** BigInt(Math.max(places, 0)),
    ];
  };
  const [rateN, rateD] = rational(rate);
  const [markupN, markupD] = rational(markup);
  const numerator = rateN * BigInt(scale) * (100n * markupD + markupN);
  const denominator = rateD * 100n * markupD;
  let remainder = numerator % denominator;
  let fraction = "";
  while (remainder) {
    remainder *= 10n;
    fraction += String(remainder / denominator);
    remainder %= denominator;
  }
  return `$${numerator / denominator}.${fraction.padEnd(2, "0")}`;
}

// MONEY-QUAL-31: stock prices preserve every stored decimal, independently
// of the product renderer. Zero retains the catalogue's explicit Free label.
export function stockMoney(value) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  if (Number(value) === 0) return "Free";
  return exactMoney(value);
}

export function tokenText(value) {
  if (!value || value <= 0) return "—";
  if (value >= 1e6) return `${(value / 1e6).toFixed(2).replace(/\.?0+$/, "")}M`;
  if (value >= 1000) return `${Math.round(value / 1000)}K`;
  return String(value);
}

const DIMENSIONS = [
  ["prompt", "Input", "1M tokens", 1e6],
  ["completion", "Output", "1M tokens", 1e6],
  ["cache_read", "Cached input", "1M tokens", 1e6],
  ["cache_write", "Cache write", "1M tokens", 1e6],
  ["reasoning", "Reasoning", "1M tokens", 1e6],
  ["image", "Image", "image", 1],
  ["generation", "Generation", "generation", 1],
];

export function inclusiveDimensions(model, markup) {
  const card = model.inclusive_price;
  if (!card || card.router_snapshot) return [];
  const rows = DIMENSIONS.map(([key, label, unit, scale]) => [
    label,
    card[key],
    unit,
    scale,
  ]);
  for (const [key, label, unit] of [
    ["request", "Request fee", "request"],
    ["web_search", "Web search", "search"],
  ]) {
    if (model.prices?.[key] > 0) rows.push([label, model.prices[key], unit, 1]);
  }
  return rows
    .filter(([, rate]) => rate != null && rate !== "")
    .map(([label, rate, unit, scale]) => ({
      label,
      unit,
      provider: exactMoney(rate, scale),
      charged: exactMoney(rate, scale, markup),
    }));
}

// Keep independent rational results as decimal strings through every step;
// never round to eight places or introduce a binary multiplication first.
const scaled = (value, scale = 1, markup = 0) =>
  exactMoney(value, scale, markup).slice(1);
const marked = (value, markup) => scaled(value, 1, markup);
const finite = (n) => typeof n === "number" && Number.isFinite(n);
const VENDORS = {
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
const vendorName = (vendor) => {
  const key = (vendor || "").replace(/^~+/, "");
  return (
    VENDORS[key] ||
    key
      .split(/[-_]/)
      .filter(Boolean)
      .map((part) =>
        /^(ai|agi|llm|ml|nlp|api)$/i.test(part)
          ? part.toUpperCase()
          : part[0].toUpperCase() + part.slice(1),
      )
      .join(" ") ||
    "Other"
  );
};

// An accessible name is a whitespace-normalized string (accname "flat
// string": each run of ASCII whitespace becomes one space, then the ends are
// trimmed), so a role + name query never sees the catalogue's stray spaces.
// The raw serializer name stays the source; only the expected name is flat.
const accessibleName = (text) => text.replace(/[\t\n\f\r ]+/g, " ").trim();

export function expectedModel(model, markup) {
  const card = model.inclusive_price;
  const qualified = Boolean(
    card &&
      card.model === model.id &&
      ["active", "degraded", "deprecated"].includes(model.state) &&
      (card.contract === "openrouter-inclusive-r601-router-1"
        ? /^[a-f0-9]{64}$/.test(card.router_snapshot || "")
        : card.contract === "openrouter-inclusive-r601-image-1" &&
          !model.dynamic_price &&
          [card.image, card.generation].some(
            (n) => Number(n) > 0 && Number.isFinite(Number(n)),
          )),
  );
  const sold =
    model.state !== "blocked" &&
    !model.unpriced &&
    !model.stale_price &&
    (!model.dynamic_price || qualified) &&
    (!card || qualified) &&
    model.managed_allowed !== false &&
    !(Date.parse(model.expiration_date) <= ORACLE_NOW) &&
    (qualified ||
      (finite(model.billed_input_per_mtok) &&
        finite(model.billed_output_per_mtok)));
  const prices = model.prices || {};
  const requestOnly =
    prices.request > 0 &&
    Object.entries(prices).every(([key, n]) => key === "request" || n === 0);
  const inclusive = sold && qualified;
  const dimensions = inclusive ? inclusiveDimensions(model, markup) : [];
  const tokenRows = [];
  const addTokens = (source, suffix = "", billed = false) => {
    for (const [key, label] of DIMENSIONS.slice(0, 4)) {
      if (!finite(source[key])) continue;
      const provider = scaled(source[key], 1000000);
      const charged =
        billed && key === "prompt"
          ? model.billed_input_per_mtok
          : billed && key === "completion"
            ? model.billed_output_per_mtok
            : undefined;
      tokenRows.push({
        label: label + suffix,
        provider,
        managed: sold ? charged ?? marked(provider, markup) : null,
      });
    }
  };
  if (model.state !== "blocked" && !inclusive) {
    addTokens(prices, "", true);
    for (const tier of model.price_tiers || [])
      addTokens(
        tier.prices || {},
        ` above ${tokenText(tier.min_prompt_tokens)} prompt tokens`,
      );
  }
  const hasTokenPrice = tokenRows.some((row) => Number(row.provider) !== 0);
  const perRequest =
    model.state === "blocked" || inclusive
      ? []
      : [
          ["request", "Request fee (per call)"],
          ["web_search", "Web search, per search"],
        ]
          .filter(([key]) => finite(prices[key]) && prices[key] > 0)
          .map(
            // One price: what a request on credits is charged when sold,
            // else the provider's. Never both, never the margin.
            ([key, label]) =>
              `${label}: ${stockMoney(sold ? marked(prices[key], markup) : prices[key])}`,
          );
  const idParts = model.id.split("/");
  const params = model.supported_parameters || [];
  const caps = [
    ["Tools", params.includes("tools")],
    [
      "Structured output",
      params.includes("structured_outputs") ||
        params.includes("response_format"),
    ],
    ["Image input", model.input_modalities?.includes("image")],
    [
      "Reasoning",
      params.includes("reasoning") || params.includes("include_reasoning"),
    ],
  ]
    .filter(([, yes]) => yes)
    .map(([label]) => label);
  return {
    id: model.id,
    name: accessibleName((model.name || model.id).replace(/^.+?: /, "")),
    href: `/dashboard/gateway/models/${encodeURIComponent(idParts.shift())}/${encodeURIComponent(idParts.join("/"))}`,
    sold,
    inclusive,
    router: inclusive && Boolean(card.router_snapshot),
    dimensions,
    vendor: vendorName(model.vendor),
    released:
      model.created > 0
        ? new Date(model.created * 1000).toLocaleDateString("en-US", {
            month: "short",
            day: "numeric",
            year: "numeric",
          })
        : "—",
    reason: sold
      ? ""
      : model.state === "blocked" && model.state_reason
        ? model.state_reason
        : model.state === "blocked" ||
            model.unpriced ||
            model.stale_price ||
            (model.dynamic_price && !qualified)
          ? `Not available on Future AGI credits: ${model.unpriced ? "unpriced" : model.stale_price ? "price out of date" : model.dynamic_price ? "priced per request" : "blocked"}. Use your own provider key.`
          : Date.parse(model.expiration_date) <= ORACLE_NOW
            ? `This model was retired on ${model.expiration_date}, so the gateway refuses it.`
            : model.managed_allowed === false
              ? "The gateway does not offer this model on Future AGI credits."
              : "The catalog has no fixed price for this model, so Future AGI credits cannot pay for it.",
    catalogPrices: inclusive
      ? dimensions.length
        ? dimensions.map((r) => `${r.label}: ${r.charged} / ${r.unit}`)
        : ["Varies"]
      : [
          sold
            ? requestOnly
              ? `${stockMoney(marked(prices.request, markup))} / request`
              : stockMoney(model.billed_input_per_mtok)
            : "—",
          sold && !requestOnly ? stockMoney(model.billed_output_per_mtok) : "—",
        ],
    // One price column: on credits the charged price, else the provider's.
    pricingHeaders:
      inclusive && dimensions.length
        ? ["Billing unit", "Price"]
        : hasTokenPrice
          ? ["Per 1M tokens", "Price"]
          : [],
    pricingRows: inclusive
      ? dimensions.map((r) => [`${r.label} (per ${r.unit})`, r.charged])
      : hasTokenPrice
        ? tokenRows.map((r) => [
            r.label,
            stockMoney(sold ? r.managed : r.provider),
          ])
        : [],
    perRequest,
    emptyPrice:
      model.state === "blocked"
        ? "Pricing is unavailable for this model."
        : "The catalog lists no per-token price for this model.",
    description: (model.description || "")
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .replace(/`([^`]+)`/g, "$1"),
    context: tokenText(model.context_length),
    maxOutput: tokenText(model.max_output_tokens),
    inputModalities: model.input_modalities?.join(", ") || "—",
    outputModalities: model.output_modalities?.join(", ") || "—",
    capabilities: caps,
    parameters: params,
    retirement:
      model.expiration_date ||
      /^expires:(\d{4}-\d{2}-\d{2})$/.exec(model.state_reason || "")?.[1] ||
      null,
    deprecated: model.state === "deprecated",
  };
}

// Independent billing-copy contract, not imported from product columns/copy.
// Prices are one number, what a request on credits is charged: the copy
// states no markup, percentage or provider price beside it.
const outputHeader = {
  name: "Output / 1M",
  tooltip:
    "Price per 1M output tokens on Future AGI credits: what a request is charged.",
};
const ownKeyBilling =
  "Requests through your own providers are billed by your provider.";
const catalogueChrome = {
  stock: {
    input: {
      name: "Input / 1M",
      tooltip:
        "Price per 1M input tokens on Future AGI credits: what a request is charged.",
    },
    output: outputHeader,
    footer:
      "Prices are per 1M tokens: what requests on Future AGI credits are billed. " +
      ownKeyBilling,
  },
  inclusive: {
    input: {
      name: "Price / billing unit",
      tooltip:
        "Prices on Future AGI credits, in each model's stated billing unit; token prices are per 1M. Inclusive models are billed from the usage the provider reports, not a sum of dimensions.",
    },
    output: outputHeader,
    footer:
      "Prices are what requests on Future AGI credits are billed. Token prices are per 1M tokens; inclusive quotes use each stated billing unit. Inclusive requests are billed from the usage the provider reports, not a sum of quoted dimensions. " +
      ownKeyBilling,
  },
};

export function loadOracle() {
  const stock = read("stock-catalog.json");
  const inclusive = read("inclusive-catalog.json");
  // The retired bytes are NOT a v2 render baseline. Read ONLY their model
  // keys as the frozen 463-model inventory; never execute or update them.
  const archive = readFileSync(
    join(fixtures, "../__snapshots__/ModelStockSnapshot.test.jsx.snap"),
    "utf8",
  );
  const ids = sorted(
    [...archive.matchAll(/^ {2}"([^"\n]+)": \{$/gm)].map((m) => m[1]),
  );
  if (ids.length !== 463 || new Set(ids).size !== 463)
    throw new Error("Legacy inventory must contain exactly 463 unique IDs");
  const catalogues = {};
  for (const [name, catalog] of Object.entries({ stock, inclusive })) {
    if (!same(sorted(catalog.models.map((m) => m.id)), ids))
      throw new Error(`${name}: missing, extra or duplicate model`);
    if (catalog.markup_percent !== 5)
      throw new Error(`${name}: unexpected markup`);
    catalogues[name] = Object.fromEntries(
      catalog.models.map((m) => [
        m.id,
        expectedModel(m, catalog.markup_percent),
      ]),
    );
  }
  if (stock.models.some((m) => m.inclusive_price))
    throw new Error("Stock must remain CAT2-OFF");
  if (inclusive.models.filter((m) => m.inclusive_price).length !== 34)
    throw new Error("Expected all 34 inclusive qualifications");
  return {
    schema: "cat2-v2-oracle-2",
    now: ORACLE_NOW,
    ids,
    catalogues,
    catalogueChrome,
  };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  process.stdout.write(`${JSON.stringify(loadOracle(), null, 2)}\n`);
}
