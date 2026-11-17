// How a run's spans were produced, in the words the catalog tile uses
// (options-truth L2 R1). One helper so Run details, the source line and the
// provenance card never disagree with the tile's badge:
//   traceAI        — a traceAI package instruments the framework
//   OpenTelemetry  — the framework's own OTel spans (no traceAI package)
//   manual spans   — the user's code opens spans by hand
import { catalogEntry } from "src/sections/common/RunSources/catalog";

export const TRACING_WORDS = Object.freeze({
  auto: "traceAI",
  otel: "OpenTelemetry",
  manual: "manual spans",
});

// Catalog badge -> config.framework.tracing (same table as runConfigFromDraft).
const BADGE_TRACING = Object.freeze({
  auto: "auto",
  otel_preview: "otel",
  otel: "otel",
  manual: "manual",
});

/**
 * `auto` | `otel` | `manual` for a code run: the config's own
 * `framework.tracing` first, else the catalog entry's badge, else `auto`.
 * @param {object} run  canonical Run with `config`
 */
export function tracingOf(run) {
  const c = run?.config ?? {};
  const t = c.framework?.tracing;
  if (TRACING_WORDS[t]) return t;
  const entry = catalogEntry(c.framework?.id ?? c.code?.framework_id ?? null);
  return BADGE_TRACING[entry?.badge] ?? "auto";
}

/** `traceAI` | `OpenTelemetry` | `manual spans`. */
export const tracingWord = (run) => TRACING_WORDS[tracingOf(run)];
