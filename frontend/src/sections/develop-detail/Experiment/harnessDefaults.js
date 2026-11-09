import {
  HARNESSES as PREVIEW_HARNESSES,
  SEED_INSTRUCTIONS as PREVIEW_INSTRUCTIONS,
} from "src/test-fixtures/build-data/harnessDefaults";

const PROTOTYPE_BUILD =
  import.meta.env.VITE_ENVIRONMENT === "local" &&
  import.meta.env.VITE_PROTOTYPE_MODE === "true";
const INCLUDE_PREVIEW = PROTOTYPE_BUILD || import.meta.env.MODE === "test";

// Agent harness card data (R2-SPEC §5.3.4): harnesses, their models, the
// sandbox from the dataset environment and the seeded instructions file.
// Pure.
import { HARNESS_PROFILES } from "src/sections/common/RunSources/recipe/recipeModel";

// One model list per harness: the recipe's (HARNESS_PROFILES), so the R2
// card and the Agent harness drawer offer the same models (options-truth L2 G6).
const modelsOf = (id) => [...(HARNESS_PROFILES[id]?.models ?? [])];

export const HARNESSES = INCLUDE_PREVIEW
  ? PREVIEW_HARNESSES.map((h) => ({ ...h, models: modelsOf(h.id) }))
  : [];
export const CUSTOM_HARNESS_HELP =
  "Any CLI agent. We run it in a sandbox per row and route its model calls through Future AGI so every call is traced.";
export const HOW_IT_RUNS =
  "How it runs: one sandbox per row and trial; the harness's model calls go through the Future AGI gateway, so each attempt is one trace with every model and tool call.";
export const DEFAULT_SANDBOX_IMAGE = "python:3.12-slim";

export const harnessOf = (name) =>
  HARNESSES.find((h) => h.value === name) ?? HARNESSES[0];

/** The model list of a harness (`the coding harness Code` → `claude-sonnet-4-5`, …). */
export const modelsFor = (name) => harnessOf(name).models;

/** `ghcr.io/acme/acme-support-sandbox:1.4` → `acme-support-sandbox:1.4`. */
export const imageName = (image) =>
  String(image ?? "")
    .split("/")
    .pop();

/**
 * Sandbox and tools from the dataset environment (task suites), else the
 * default sandbox with no MCP tools.
 */
export function sandboxFrom(environment) {
  if (!environment?.image)
    return {
      fromDataset: false,
      sandbox: `Default sandbox · ${DEFAULT_SANDBOX_IMAGE}`,
      resources: null,
      mcp: null,
      secrets: [],
    };
  const r = environment.resources ?? {};
  return {
    fromDataset: true,
    sandbox: `Docker · ${imageName(environment.image)}`,
    resources:
      r.cpu || r.memory_gb
        ? [
            r.cpu ? `${r.cpu} CPU` : null,
            r.memory_gb ? `${r.memory_gb} GB` : null,
          ]
            .filter(Boolean)
            .join(" · ")
        : null,
    mcp: environment.mcp
      ? {
          name: environment.mcp.name,
          tools: Number(environment.mcp.tools) || 0,
          tool_names: environment.mcp.tool_names ?? [],
        }
      : null,
    secrets: (environment.secrets ?? [])
      .map((s) => (typeof s === "string" ? s : s?.name))
      .filter(Boolean),
  };
}

export const wordCount = (text) =>
  String(text ?? "")
    .split(/\s+/)
    .filter(Boolean).length;

export const SEED_INSTRUCTIONS = INCLUDE_PREVIEW ? PREVIEW_INSTRUCTIONS : "";
