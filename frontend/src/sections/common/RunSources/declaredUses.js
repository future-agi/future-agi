// IT-R4 G1 P5: what a Your code run uses, declared by the user (optional):
// a model, tools and MCP servers, so Review can list them like harness runs
// and estimate the model cost. Pure.

/**
 * USD per 1M tokens (input, output), public list prices. Covers every model
 * a picker offers (declared uses, harness recipes, prompts, voice, image),
 * so the Review estimate and the run's cost read one table (C10).
 */
export const MODEL_PRICES = Object.freeze({
  "gpt-4.1": [2, 8],
  "gpt-4.1-mini": [0.4, 1.6],
  "gpt-4o": [2.5, 10],
  "gpt-4o-mini": [0.15, 0.6],
  "gpt-5": [1.25, 10],
  "gpt-5-mini": [0.25, 2],
  "gpt-5-codex": [1.25, 10],
  o3: [2, 8],
  "o4-mini": [1.1, 4.4],
  "claude-opus-4-1": [15, 75],
  "claude-sonnet-4-5": [3, 15],
  "claude-sonnet-4-20250514": [3, 15],
  "claude-haiku-4-5": [1, 5],
  "gemini-2.5-flash": [0.3, 2.5],
  "gemini-2.5-pro": [1.25, 10],
  // speech and image models priced per token
  "gpt-4o-mini-tts": [0.6, 12],
  "gpt-4o-transcribe": [6, 10],
  "gpt-image-1": [5, 40],
  "gemini-2.5-flash-image": [0.3, 30],
});
/**
 * Speech and image models priced per unit (public list prices): USD per
 * 1K characters (TTS), per audio minute (STT) or per image.
 */
export const UNIT_PRICES = Object.freeze({
  "tts-1-hd": { per: "1k_chars", usd: 0.03 },
  eleven_multilingual_v2: { per: "1k_chars", usd: 0.1 },
  eleven_turbo_v2_5: { per: "1k_chars", usd: 0.05 },
  "whisper-1": { per: "minute", usd: 0.006 },
  "nova-2": { per: "minute", usd: 0.0043 },
  "nova-3": { per: "minute", usd: 0.0043 },
  "dall-e-3": { per: "image", usd: 0.04 },
  "gpt-image-1": { per: "image", usd: 0.042 },
  "gemini-2.5-flash-image": { per: "image", usd: 0.039 },
  "imagen-4": { per: "image", usd: 0.04 },
});
/**
 * p50 milliseconds of one agent try (TOKENS_PER_TRY, a few tool calls) per
 * model, for the duration estimate and the run engine's playback (C10).
 * Faster tiers answer sooner; reasoning tiers think before they answer.
 */
export const MODEL_LATENCY_MS = Object.freeze({
  "gpt-4.1": 9000,
  "gpt-4.1-mini": 6000,
  "gpt-4o": 8000,
  "gpt-4o-mini": 5000,
  "gpt-5": 18000,
  "gpt-5-mini": 11000,
  "gpt-5-codex": 20000,
  o3: 22000,
  "o4-mini": 12000,
  "claude-opus-4-1": 21000,
  "claude-sonnet-4-5": 12000,
  "claude-sonnet-4-20250514": 12000,
  "claude-haiku-4-5": 6000,
  "gemini-2.5-flash": 6000,
  "gemini-2.5-pro": 15000,
  "gpt-4o-mini-tts": 1500,
  "gpt-4o-transcribe": 1800,
  "gpt-image-1": 14000,
  "gemini-2.5-flash-image": 8000,
  "tts-1-hd": 1600,
  eleven_multilingual_v2: 1200,
  eleven_turbo_v2_5: 700,
  "whisper-1": 2000,
  "nova-2": 900,
  "nova-3": 800,
  "dall-e-3": 12000,
});
/** p50 of one try at a model, or null when the model is unknown. */
export const latencyOf = (model) =>
  MODEL_LATENCY_MS[String(model ?? "").trim()] ?? null;

/** The key a model's vendor needs (secrets, CI), or null. */
export function vendorKeyOf(model) {
  const m = String(model ?? "")
    .trim()
    .toLowerCase();
  if (!m) return null;
  if (/^(gpt|o\d|whisper|tts-|dall-e|text-embedding)/.test(m))
    return "OPENAI_API_KEY";
  if (m.startsWith("claude")) return "ANTHROPIC_API_KEY";
  if (m.startsWith("gemini")) return "GEMINI_API_KEY";
  if (m.startsWith("eleven")) return "ELEVENLABS_API_KEY";
  if (m.startsWith("nova")) return "DEEPGRAM_API_KEY";
  return null;
}

/** The model keys a run needs, one per vendor, in a stable order. */
export function vendorKeysOf(models = []) {
  const keys = [];
  models.forEach((m) => {
    const k = vendorKeyOf(typeof m === "string" ? m : m?.id ?? m?.name);
    if (k && !keys.includes(k)) keys.push(k);
  });
  return keys;
}

export const DECLARED_MODELS = Object.keys(MODEL_PRICES).filter(
  (m) => !/(tts|transcribe|image)/.test(m),
);

/** Tokens one agent try spends, for planning (an agent task with tools). */
export const TOKENS_PER_TRY = Object.freeze({ input: 7000, output: 1900 });

// ------------------------------------------------ the engine's cost model
// options-truth L5 G3 (one estimator): the tables the run engine prices and
// times a try with live here, so the estimate and the run read one model.
// fixtures/world/runEngineProfiles.js re-exports them under the same names.

/** List prices for models MODEL_PRICES may not list yet (USD / 1M). */
export const EXTRA_MODEL_PRICES = Object.freeze({
  "claude-opus-4-1": [15, 75],
  "gpt-5-codex": [1.25, 10],
  "o4-mini": [1.1, 4.4],
});

/** p50 of one model call (ms), used as a latency ratio between models. */
export const FALLBACK_MODEL_LATENCY_MS = Object.freeze({
  "gpt-4.1": 1900,
  "gpt-4.1-mini": 1300,
  "gpt-4o": 1700,
  "gpt-4o-mini": 1100,
  "gpt-5": 3600,
  "gpt-5-mini": 2100,
  "gpt-5-codex": 3200,
  "o4-mini": 2600,
  "claude-opus-4-1": 4200,
  "claude-sonnet-4-5": 2400,
  "claude-haiku-4-5": 1200,
  "gemini-2.5-flash": 1000,
  "gemini-2.5-pro": 2900,
});

/**
 * Per-kind token profile of a platform try: tokens as a multiple of
 * TOKENS_PER_TRY (`token_scale`), the p50 of one try at the kind's default
 * model, and that model. A prompt try is one model call (~1,070 tokens), an
 * agent graph try a few node calls (~3,560 tokens).
 */
export const KIND_TOKEN_PROFILES = Object.freeze({
  prompt: Object.freeze({
    token_scale: 0.12,
    p50_ms: 1600,
    default_model: "gpt-4o-mini",
  }),
  agent: Object.freeze({
    token_scale: 0.4,
    p50_ms: 3800,
    default_model: "gpt-4o",
  }),
});

/** USD per 1M `[input, output]` of a model (custom models: their own). */
export function modelPriceOf(model, customPrices = null) {
  const m = String(model ?? "").trim();
  if (!m) return null;
  return (
    MODEL_PRICES[m] ??
    EXTRA_MODEL_PRICES[m] ??
    (customPrices && Array.isArray(customPrices[m]) ? customPrices[m] : null)
  );
}

/** The engine's latency of one model call (ms), or null when unknown. */
const callMsOf = (m) =>
  MODEL_LATENCY_MS[m] ?? FALLBACK_MODEL_LATENCY_MS[m] ?? null;

/**
 * Expected cost and time of one platform try, as the run engine computes
 * it (runEngine.attemptOf at its mean token jitter): the kind's tokens at
 * the model's prices, completion tokens never above `maxTokens`, an agent's
 * llm nodes each at their own model (the last node reads 60% and writes
 * 85%). Null for a kind without a profile or a model without a price.
 * @param {{kind: "prompt"|"agent", model?: string, maxTokens?: number,
 *   nodeModels?: string[], customPrices?: object}} p
 * @returns {{usd: number, ms: number, tokens: {input: number, output: number}}|null}
 */
export function engineTry({
  kind,
  model,
  maxTokens = null,
  nodeModels = null,
  customPrices = null,
} = {}) {
  const prof = KIND_TOKEN_PROFILES[kind];
  if (!prof) return null;
  const nodes = (nodeModels ?? []).filter(Boolean);
  const main =
    String(model ?? "").trim() || nodes[nodes.length - 1] || prof.default_model;
  const input = TOKENS_PER_TRY.input * prof.token_scale;
  let output = TOKENS_PER_TRY.output * prof.token_scale;
  const cap = Number(maxTokens);
  if (Number.isFinite(cap) && cap > 0) output = Math.min(output, cap);
  let usd = 0;
  if (kind === "agent" && nodes.length > 1) {
    const k = nodes.length;
    for (let i = 0; i < k; i += 1) {
      const pr = modelPriceOf(nodes[i], customPrices);
      if (!pr) return null;
      const last = i === k - 1;
      const inShare = last ? 0.6 : 0.4 / (k - 1);
      const outShare = last ? 0.85 : 0.15 / (k - 1);
      usd += (input * inShare * pr[0] + output * outShare * pr[1]) / 1e6;
    }
  } else {
    const pr = modelPriceOf(main, customPrices);
    if (!pr) return null;
    usd = (input * pr[0] + output * pr[1]) / 1e6;
  }
  const ratio =
    (callMsOf(main) ?? callMsOf(prof.default_model)) /
    callMsOf(prof.default_model);
  return {
    usd,
    ms: Math.round(prof.p50_ms * ratio),
    tokens: { input: Math.round(input), output: Math.round(output) },
  };
}

/**
 * USD of one media output at a unit-priced model: a TTS clip of `chars`
 * characters, an STT transcript of `minutes`, one image. Null when the
 * model has no unit price.
 */
export function unitCostOf(model, { chars = 0, minutes = 0 } = {}) {
  const u = UNIT_PRICES[String(model ?? "").trim()];
  if (!u) return null;
  if (u.per === "1k_chars") return (Math.max(0, chars) / 1000) * u.usd;
  if (u.per === "minute") return Math.max(0, minutes) * u.usd;
  return u.usd;
}

/** USD one try costs at a model's list prices, or null when unknown. */
export function costPerTry(model) {
  const p = MODEL_PRICES[String(model ?? "").trim()];
  if (!p) return null;
  return (TOKENS_PER_TRY.input * p[0] + TOKENS_PER_TRY.output * p[1]) / 1e6;
}

/** `{model, tools, mcp}` with empty parts dropped, or null when empty. */
export function cleanDeclared(d) {
  const model = String(d?.model ?? "").trim();
  const tools = (d?.tools ?? []).filter(Boolean);
  const mcp = (d?.mcp ?? []).filter(Boolean);
  if (!model && !tools.length && !mcp.length) return null;
  return { model: model || null, tools, mcp };
}

/** `gpt-4.1 · 3 tools · MCP acme-support-sandbox` (pure). */
export function declaredSummary(d) {
  const c = cleanDeclared(d);
  if (!c) return null;
  return [
    c.model,
    c.tools.length
      ? `${c.tools.length} ${c.tools.length === 1 ? "tool" : "tools"}`
      : null,
    c.mcp.length ? `MCP ${c.mcp.join(", ")}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}
