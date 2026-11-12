// How Gateway settings reads what the gateway reports about itself in GET
// /-/config (server, logging and batch blocks, the last health check and the
// org sections it enforces), passed through by the config read. A value the
// gateway does not report reads "Not reported", never a default.

import { sectionEnforcement } from "./sectionEnforcement";

export const NOT_REPORTED = "Not reported";

export const isBlock = (value) =>
  value != null && typeof value === "object" && !Array.isArray(value);

export function formatMs(ms) {
  if (typeof ms !== "number" || !Number.isFinite(ms)) return NOT_REPORTED;
  if (Math.abs(ms) < 1000) return `${ms} ms`;
  const seconds = ms / 1000;
  return `${Number.isInteger(seconds) ? seconds : seconds.toFixed(1)} s`;
}

export function formatBytes(bytes) {
  if (typeof bytes !== "number" || !Number.isFinite(bytes)) return NOT_REPORTED;
  const MiB = 1024 * 1024;
  if (bytes >= MiB && bytes % MiB === 0) return `${bytes / MiB} MiB`;
  if (bytes >= 1024 && bytes % 1024 === 0) return `${bytes / 1024} KiB`;
  return `${bytes} bytes`;
}

export const onOff = (value, on, off) =>
  typeof value === "boolean" ? (value ? on : off) : NOT_REPORTED;

// When the organization last checked the gateway's health and what it found:
// the config read's record, else the gateway list's.
export function lastHealthCheckText(config, gateway) {
  const fromConfig = config?.last_health_check;
  const at = fromConfig ?? gateway?.lastHealthCheck ?? null;
  if (!at) return "No check recorded";
  const outcome = fromConfig
    ? config?.last_health_check_status
    : gateway?.last_health_check_status ?? gateway?.lastHealthCheckStatus;
  const when = new Date(at).toLocaleString();
  return outcome ? `${when} (${outcome})` : when;
}

export function enforcedSectionsText(config) {
  const enforced = config?.org_sections_enforced;
  if (!Array.isArray(enforced) && guardrailsOn(config) !== true) {
    return "Not reported (enforcement unknown)";
  }
  const sections = new Set(
    (enforced || []).filter((section) =>
      section === "guardrails"
        ? guardrailsOn(config) === true
        : sectionEnforcement({ data: config }, section) !== "not_enforced",
    ),
  );
  if (guardrailsOn(config) === true) sections.add("guardrails");
  return sections.size > 0 ? [...sections].join(", ") : "None on this gateway";
}

// Whether this gateway runs guardrails (guardrail_pipeline.enabled; the
// gateway lists no guardrails entry in org_sections_enforced) and response
// caching (cache in org_sections_enforced). null: it does not say.
function guardrailsOn(config) {
  const node = config?.guardrail_pipeline;
  return isBlock(node) && typeof node.enabled === "boolean"
    ? node.enabled
    : null;
}
function cacheOn(config) {
  const state = sectionEnforcement({ data: config }, "cache");
  return state === "unknown" ? null : state === "enforced";
}

// "section: reason" for each org section the gateway does not enforce: each
// one it names with a reason (org_sections_not_enforced), guardrails while
// its guardrail pipeline is off, and cache while it runs no response cache.
// null when there is none.
export function notEnforcedSectionsText(config) {
  const reasons = isBlock(config?.org_sections_not_enforced)
    ? config.org_sections_not_enforced
    : {};
  const entries = Object.entries(reasons).filter(
    ([, reason]) => typeof reason === "string" && reason,
  );
  const named = new Set(entries.map(([section]) => section));
  if (guardrailsOn(config) === false && !named.has("guardrails")) {
    entries.push(["guardrails", "guardrails are off on this gateway"]);
  }
  if (cacheOn(config) === false && !named.has("cache")) {
    entries.push(["cache", "response caching is off on this gateway"]);
  }
  if (entries.length === 0) return null;
  return entries.map(([section, reason]) => `${section}: ${reason}`).join("; ");
}

// The Submit batch note: what each request of a batch goes through, naming
// only the stages this gateway applies.
export function batchPipelineNote(config) {
  const stages = ["routing"];
  if (guardrailsOn(config) === true) stages.push("guardrails");
  if (cacheOn(config) === true) stages.push("caching");
  return `Each request is sent through the gateway pipeline (${stages.join(", ")}) independently. Results include per-request cost and token usage.`;
}
