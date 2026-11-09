// Bundled run-source logos (owner, Oct 20: "correct logo icons for the coding
// agent / frameworks"). Every catalog entry's mark ships in the bundle as an
// SVG string (`?raw`), so no logo is ever fetched at runtime (the old Iconify
// brand icons came from the Iconify API and fell back to monograms offline).
//
// Each SVG is the brand's own mark, taken from the URL in `source` (the
// project's repo, docs or site, or an icon set that copies the official mark:
// simple-icons CC0, lobe-icons MIT, Material Design Icons Apache-2.0). Edits
// are limited to removing a wordmark or background tile, recolouring to
// `currentColor`, and cropping to a square viewBox (tiles keep a 4% inset).
// One file is composed: Aider's official pinned-tab shape (a dark tile with
// the "a" knocked out) sits over a square in Aider's official #14B014, so the
// "a" shows green as on aider.chat's favicon; no path was redrawn.
// The marks belong to their owners; repository licences cover code and docs,
// not trademarks. We use a mark only to name the integration a user picks.
//
//   mode "mono"   one colour = currentColor (the entry's `logo.color`, else
//                 text.primary), so it follows light / dark mode
//   mode "colour" keeps its official colours
//   mode "png"    no official SVG exists; the official PNG, inlined
//   scale         optical size in its box (1 = fills it)
//   darkEdge      a dark tile that needs a faint edge on a dark background
//   generic       a concept icon (HTTP, upload, phone), not a brand
import smolagentsPng from "src/assets/brand-logos/smolagents-png.js";
import { entryOfConfig } from "./catalog";

const MARK_FILES = import.meta.glob("../../../assets/brand-logos/*.svg", {
  query: "?raw",
  import: "default",
  eager: true,
});
const GENERIC_FILES = import.meta.glob(
  "../../../assets/brand-logos/generic/*.svg",
  { query: "?raw", import: "default", eager: true },
);

const byName = (files) =>
  Object.fromEntries(
    Object.entries(files).map(([path, svg]) => [
      path
        .split("/")
        .pop()
        .replace(/\.svg$/, ""),
      svg,
    ]),
  );
const SVG = byName(MARK_FILES);

const GH = "https://github.com";
const LOBE = "https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@1.95.1";
const SI = "https://cdn.jsdelivr.net/npm/simple-icons@16.35.0/icons";
const MDI = "https://cdn.jsdelivr.net/npm/@mdi/svg@7.4.47/svg";
const CLAUDE =
  "https://mintcdn.com/claude-code/c5r9_6tjPMzFdDDT/logo/light.svg";
const OPENAI = `${GH}/openai/openai-agents-python/blob/64cd5dfc5a15/docs/assets/logo.svg`;

const mark = (mode, source, licence, extra = {}) => ({
  mode,
  source,
  licence,
  ...extra,
});
const mdi = (name) =>
  mark("mono", `${MDI}/${name}.svg`, "MDI Apache-2.0", { generic: true });

/** entryId → mark facts (the SVG string is attached below). */
const MARKS = {
  // Frameworks
  langgraph: mark(
    "colour",
    `${GH}/langchain-ai/langgraph/blob/a24fc97778fc/.github/images/logo-light.svg`,
    "repo MIT; mark (c) LangChain, Inc.",
    { darkEdge: true },
  ),
  langchain: mark(
    "colour",
    `${GH}/langchain-ai/langchain/blob/afafd55d34cb/.github/images/logo-light.svg`,
    "repo MIT; mark (c) LangChain, Inc.",
    { darkEdge: true },
  ),
  llamaindex: mark(
    "colour",
    "https://www.llamaindex.ai/ (header logo inline SVG, wordmark removed; = favicon.svg)",
    "mark (c) LlamaIndex",
    { darkEdge: true },
  ),
  crewai: mark(
    "mono",
    `${GH}/crewAIInc/crewAI/blob/4f4573706ea4/docs/images/favicon.svg`,
    "repo MIT; mark (c) CrewAI Inc.",
  ),
  openai_agents: mark("mono", OPENAI, "repo MIT; OpenAI logo (c) OpenAI"),
  google_adk: mark(
    "colour",
    `${GH}/google/adk-python/blob/8909689ae46d/src/google/adk/cli/browser/adk_favicon.svg`,
    "repo Apache-2.0; ADK mark (c) Google",
  ),
  autogen: mark(
    "colour",
    `${GH}/microsoft/autogen/blob/004749e6b710/python/docs/src/_static/images/logo/logo.svg`,
    "repo CC-BY-4.0 docs / MIT code; mark (c) Microsoft",
    { darkEdge: true },
  ),
  smolagents: mark(
    "png",
    "https://huggingface.co/datasets/huggingface/documentation-images/resolve/main/smolagents/mascot_smol.png",
    "repo Apache-2.0; mascot (c) Hugging Face",
    { src: smolagentsPng },
  ),
  dspy: mark(
    "mono",
    `${GH}/stanfordnlp/dspy/blob/319aed593089/docs/docs/static/dspy-logo.svg`,
    "repo MIT; mark (c) DSPy / Stanford NLP",
  ),
  haystack: mark(
    "colour",
    `${GH}/deepset-ai/haystack/blob/68c782c5085b/docs-website/static/img/logo.svg`,
    "repo Apache-2.0; mark (c) deepset GmbH",
  ),
  mastra: mark(
    "mono",
    `${GH}/mastra-ai/mastra/blob/62b6ba854448/mastracode/factory-ui/src/ui/public/mastra.svg (= https://mastra.ai/favicon/new-brand/icon.svg)`,
    "Apache-2.0 except ee/; mark (c) Mastra",
  ),
  vercel_ai: mark(
    "mono",
    `${GH}/vercel/ai/blob/f2876d0b4851/apps/docs/app/icon.svg (= https://ai-sdk.dev/icon.svg, tile removed)`,
    "repo Apache-2.0; Vercel logo (c) Vercel",
    { scale: 0.9 },
  ),
  pydantic_ai: mark(
    "mono",
    `${GH}/pydantic/pydantic-ai/blob/f545a78ae959/docs/img/logo-white.svg`,
    "repo MIT; mark (c) Pydantic Services Inc.",
  ),
  agno: mark(
    "colour",
    "https://docs.agno.com/favicon.svg",
    "mark (c) Agno Inc.",
  ),
  strands: mark(
    "colour",
    `${GH}/strands-agents/harness-sdk/blob/6b0368f19b9a/site/public/favicon.svg (= https://strandsagents.com/favicon.svg)`,
    "repo Apache-2.0; mark (c) Amazon / Strands Agents",
    { darkEdge: true },
  ),
  claude_agent_sdk: mark(
    "mono",
    `${CLAUDE} (code.claude.com docs logo)`,
    "Claude mark (c) Anthropic PBC",
  ),
  ag2: mark(
    "mono",
    `${GH}/ag2ai/ag2/blob/0dc64cdd5f69/website/logo/favicon.svg (= https://ag2.ai/favicondark.svg)`,
    "repo Apache-2.0; mark (c) AG2AI",
  ),
  custom_python: mark(
    "colour",
    "https://www.python.org/static/community_logos/python-logo-generic.svg (text removed)",
    "Python logo is a PSF trademark",
  ),
  custom_typescript: mark(
    "colour",
    `${GH}/microsoft/TypeScript-Website/blob/04569788cf46/packages/typescriptlang-org/static/branding/ts-logo-128.svg`,
    "repo CC-BY-4.0; TypeScript logo (c) Microsoft",
  ),
  otel_app: mark(
    "colour",
    `${GH}/cncf/artwork/blob/b4b5989332c8/projects/opentelemetry/icon/color/opentelemetry-icon-color.svg`,
    "CNCF artwork; OpenTelemetry mark (c) The Linux Foundation",
  ),
  // Coding-agent harnesses
  claude_code: mark(
    "mono",
    `${CLAUDE} (code.claude.com docs logo and favicon)`,
    "Claude mark (c) Anthropic PBC",
  ),
  // Owner ruling (logos REVIEW-r3 §1): the coding harness shows the OpenAI blossom, the
  // same file as the Agents SDK, so sharesMark keeps the name beside both.
  codex_cli: mark(
    "mono",
    `${OPENAI} (the OpenAI blossom, as on the Agents SDK; file openai_agents.svg)`,
    "repo MIT; OpenAI logo (c) OpenAI",
    { file: "openai_agents" },
  ),
  gemini_cli: mark(
    "colour",
    `${LOBE}/icons/geminicli-color.svg (checked against https://geminicli.com/icon.png)`,
    "lobe-icons MIT; mark (c) Google",
  ),
  openhands: mark(
    "mono",
    `${GH}/OpenHands/OpenHands/blob/e9563919e18c/src/assets/branding/openhands-logo.svg`,
    "repo MIT; mark (c) All Hands AI",
  ),
  aider: mark(
    "colour",
    `${GH}/Aider-AI/aider/blob/5dc9490bb35f/aider/website/assets/icons/safari-pinned-tab.svg (coloured as the favicon: dark tile, #14B014 "a" from aider/website/assets/logo.svg)`,
    "repo Apache-2.0; mark (c) Aider-AI",
    { darkEdge: true },
  ),
  goose: mark(
    "mono",
    `${GH}/aaif-goose/goose/blob/9920efa16d07/ui/desktop/src/images/icon.svg`,
    "repo Apache-2.0; mark (c) Block, Inc. / AAIF",
  ),
  mini_swe_agent: mark(
    "colour",
    `${GH}/SWE-agent/mini-swe-agent/blob/04d809ceab9d/docs/assets/mini_square.svg`,
    "repo MIT; mark (c) SWE-agent team",
  ),
  custom_command: mdi("console"),
  // Endpoints / already running (generic concepts, bundled the same way)
  http: mdi("api"),
  a2a: mdi("card-account-details-outline"),
  mcp_tool: mdi("tools"),
  traces: mdi("transit-connection-variant"),
  upload: mdi("upload-outline"),
};

// sha256 of each bundled file exactly as imported (?raw text; smolagents =
// its data URI), recorded with dsx/logos/build-r2/scripts/shas.mjs. This is a
// change detector, not proof of provenance: the test recomputes it, so a file
// that changes fails until someone records a new hash, and recording one is
// the moment to check the file against its `source` row above again. The
// sha256 of each untouched upstream download sits next to its row in
// dsx/logos/SOURCES.md, so a reviewer can rebuild a file from its source.
const SHA256 = {
  a2a: "f343d48aee2268e39dc1fc2153e0e176ef1218abc90c158b554674062d852302",
  ag2: "b699db1ed425a54eb5dd487db2fc847c46e1aa4a37d9352514e7dbcacf767939",
  agno: "ddd9cbc26507c1e5529a13803f4b969003b6518334766c0b888b443f751b946f",
  aider: "b30d3ea1c20519991f5cc125f2371e7486872091cbb74d29db4887b15f73ec58",
  autogen: "ae680ee0ec9e5428fc400fed290a864a7bad0445795d9bcf385ee697cc16703f",
  claude_agent_sdk:
    "414961a83e3898391c85b37fdfe8a068cf4a6276c85b2a2edc1cd6865c62bdc3",
  claude_code:
    "414961a83e3898391c85b37fdfe8a068cf4a6276c85b2a2edc1cd6865c62bdc3",
  codex_cli: "68e5b6214470b1a5194417d67d4956a23c4634d5058edc23d65b8b291f3bfce3",
  crewai: "80e59282c2f251230a928689df8aafec2cea113b9551947fcd72ff1e128ee50b",
  custom_command:
    "8a609974617fe0cfef309d2179a5a1460c4b8a0487e2038ff6fea63e97983bff",
  custom_python:
    "ccd8061e6a9e1c4930bcf5fa05d226dba3a923da59de6e8f62380b0cf7149d96",
  custom_typescript:
    "b4a5d270b950e31837071ac8803ab9968513e00d5d2d127f83cd30c2cd42f814",
  dspy: "8e37477a80e4810913cd9256aa39d3b4326bb82bb4a88ee0490bbffda92f8357",
  gemini_cli:
    "79e6d5aad00f5ca2e7d14edb41dc488b095d1ed5b6953cf60a4dbb0a290c8714",
  google_adk:
    "04150e32973d387f3a6b274e8d587c2ba008fe626a52b7873c3d4312313fb366",
  goose: "1706ec8e062bc2b9984d20177d155098e1bd159e242fdfada414312b457ed4e6",
  haystack: "b2ff074e7b3c2c96b3b8b14599db5fcd3780052ac8e34b9fc69ad48d5b7ac03a",
  http: "e55d3f6c0f81f84b88f34dc25bbb50c79d1ebb996115fc06e57a1f86678af106",
  langchain: "c1ef4b304fe7ab36236839454018b5d284c616beee8fde90f7c5ac5f3ab11f28",
  langgraph: "08164a4b3019c7f1bb65e5077cad740ce860ed844aebc909947f8ec148528774",
  llamaindex:
    "5724ea62381a86e012a3a2ae80d9bddd2fca655ed58b339edaa5b1cf1eab9866",
  mastra: "8a1a308c223f1fe6f04de0baa9c81a694ba7fbedf97d9841d80da831f7726b34",
  mcp_tool: "a62375588486a70a798ff1ab11a819e66c39161cd388d4e7975282a4ab3e8f94",
  mini_swe_agent:
    "d095484e559b26b7ef21e232b426d6c1be0904831f192a5319f59eb58866dab5",
  openai_agents:
    "68e5b6214470b1a5194417d67d4956a23c4634d5058edc23d65b8b291f3bfce3",
  openhands: "a40340c4997991f9ac647e36a409af7d9d1c353be29519b2bdd35478b90e4586",
  otel_app: "0651f4c08be93340af5d58b98398b32e6274e9d40606337693c4d9e951400f5c",
  pydantic_ai:
    "ca258200789997ea027d6a6cd4167fbb8ac51a2649cea9f7a9ffebe6baa974ee",
  smolagents:
    "e6c14e853a2c059a52f86cf8c59a5c2446457ba3269daa157610f271f80c5bf7",
  strands: "696a372b87fdfffafa2fb51cd50c08eb54d03dce27c1d8f175e6c66a376f180b",
  traces: "666083deec2ba379dd0eb1f6c0dcfd7ce919329f1416bf5943c9147f75d38307",
  upload: "edd3db2e880488681ba9e017a32dcd0bf854cec3bdeeb58a7ce55116248df1bb",
  vercel_ai: "7cd33e9b909863f8faa7ac0d39cbfe34b9a83479618bbaf7bc946743ce3793c2",
};

const idsOf = (svg) => /\bid="/.test(svg ?? "");

/** Every bundled mark: `{id, mode, svg?, src?, hasIds, sha256, scale, darkEdge, source, licence}`. */
export const BRAND_LOGOS = Object.freeze(
  Object.fromEntries(
    Object.entries(MARKS).map(([id, { file = id, ...m }]) => [
      id,
      Object.freeze({
        id,
        scale: 1,
        darkEdge: false,
        generic: false,
        ...m,
        svg: m.mode === "png" ? null : SVG[file] ?? null,
        hasIds: idsOf(SVG[file]),
        sha256: SHA256[id] ?? null,
      }),
    ]),
  ),
);

/** The bundled mark for an id, or null when none ships (monogram then). */
export const brandLogoOf = (id) => {
  const m = id ? BRAND_LOGOS[id] : null;
  return m && (m.svg || m.src) ? m : null;
};

// Brands whose mark is the same file as another brand's (the coding harness Code and
// the coding harness Agent SDK both use the the coding harness spark, as both products' docs do;
// the coding harness CLI and the OpenAI Agents SDK both use the OpenAI blossom).
const SHARED_MARKS = (() => {
  const byHash = {};
  Object.values(BRAND_LOGOS).forEach((m) => {
    if (!m.generic && m.sha256) (byHash[m.sha256] ??= []).push(m.id);
  });
  return new Set(
    Object.values(byHash).flatMap((ids) => (ids.length > 1 ? ids : [])),
  );
})();

/**
 * True when another brand shows the same mark, so the mark alone does not
 * say which one it is; a surface that drops the name keeps it for these.
 */
export const sharesMark = (id) => SHARED_MARKS.has(id);

/**
 * The catalog entry a run config names when that entry is a brand (the coding harness
 * Code, LangGraph), else null (prompt, agent graph, HTTP, upload...).
 * @param {object} config  run config (R1 include=config)
 * @param {string} [kind]  the run's kind when the config omits it
 */
export function brandedEntryOfConfig(config, kind) {
  const e = entryOfConfig({ ...(config ?? {}), kind: kind ?? config?.kind });
  const m = e ? brandLogoOf(e.logo?.mark ?? e.id) : null;
  return m && !m.generic ? e : null;
}

/**
 * An inlined SVG may appear many times on a page. Ids (gradients, clip
 * paths) get a per-instance suffix so no copy points at another copy's defs
 * (Chrome drops a gradient whose first definition sits in a hidden subtree).
 */
export const withUniqueIds = (svg, suffix) =>
  svg.replace(
    /(\bid="|url\(#|href="#)([^")]+)/g,
    (_, head, id) => `${head}${id}-${suffix}`,
  );

/** Generic concept icons (Material Design Icons), keyed by Iconify id. */
export const GENERIC_ICONS = Object.freeze(
  Object.fromEntries(
    Object.entries(byName(GENERIC_FILES)).map(([n, svg]) => [`mdi:${n}`, svg]),
  ),
);
