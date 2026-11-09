// What kind of run a run is (R2-SPEC §1.1, §0.3). Pure; one spelling of the
// run kinds everywhere (badges, the config diff, the default compare tab,
// the result drawer's Artifacts tab).
//
// Frozen API (R2 manifest row 43): RUN_KINDS, runKindOf, kindLabel,
// isPreviewRun, defaultRunsTab, isArtifactsRun.

/**
 * Every kind of run, in add-run menu order (R4-SPEC D1: `voice` added; the
 * New experiment cards map 1:1 to kinds, `Already running` groups `http`,
 * `traces`, `upload`).
 */
export const RUN_KINDS = Object.freeze([
  "prompt",
  "agent",
  "code",
  "harness",
  "voice",
  "http",
  "traces",
  "upload",
]);

const KIND_SET = new Set(RUN_KINDS);
const known = (kind) => (KIND_SET.has(kind) ? kind : null);

/**
 * The run's kind: `run.config.kind`, else `run.source.kind`, else
 * `run.kind`, else derived from today's run fields (an agent graph →
 * `agent`, `target_type` http → `http`, an external upload → `upload`,
 * anything else → `prompt`).
 * @param {object} run  canonical Run (R1 / experiment detail / R3 summary)
 * @returns {"prompt"|"agent"|"code"|"harness"|"voice"|"http"|"traces"|"upload"}
 */
export function runKindOf(run) {
  if (!run) return "prompt";
  const explicit =
    known(run.config?.kind) ?? known(run.source?.kind) ?? known(run.kind);
  if (explicit) return explicit;
  const target = run.target_type ?? run.targetType;
  if (target === "agent" || run.target?.graph_id || run.agent_id)
    return "agent";
  if (target === "http") return "http";
  if (target === "external") return "upload";
  return "prompt";
}

// R4 (F3 locked names): `Your code`, `Endpoint`, `Voice agent`.
const LABELS = {
  prompt: "Prompt",
  agent: "Agent",
  code: "Your code",
  harness: "Harness",
  voice: "Voice agent",
  http: "Endpoint",
  traces: "Traces",
  upload: "Upload",
};

/**
 * The short word for a kind: `Prompt` / `Prompt + tools` / `Agent` /
 * `Your code` / `Harness` / `Voice agent` / `Endpoint` / `Traces` / `Upload`.
 * @param {string} kind
 * @param {{tools?: boolean|number|Array}} [opts]  truthy (or a non-empty
 *   list) when a prompt run calls tools
 */
export function kindLabel(kind, { tools } = {}) {
  const hasTools = Array.isArray(tools) ? tools.length > 0 : Boolean(tools);
  if (kind === "prompt" && hasTools) return "Prompt + tools";
  return LABELS[kind] ?? LABELS.prompt;
}

/**
 * dataset integration FE-0 (owner Q12): the kinds Future AGI can start again itself. A
 * code, harness, voice or uploaded run came from a generated runner or a
 * live connection that does not exist, so Rerun and Rerun with changes are
 * absent for it until the SDK command is green (FE-1 prints
 * `futureagi run TARGET --rows failed:RUN`).
 */
const RERUN_HERE = new Set(["prompt", "agent", "http"]);
export const canRerunHere = (run) => RERUN_HERE.has(runKindOf(run));

// Code paths through the unreleased SDK (V12): the R2 SDK run and the R4 live connection.
const PREVIEW_CODE_MODES = new Set(["sdk_preview", "connect_preview"]);
// Self-hosted voice connectors (dataset integration P4b): Preview until their rows pass.
const PREVIEW_VOICE = new Set([
  "websocket",
  "livekit",
  "pipecat",
  "webrtc",
  "worker",
]);

/**
 * Preview runs carry the grey `Preview` word (R4-SPEC §9): harness runs
 * always; code runs through the unreleased SDK (an SDK run or a live connection) or
 * in the Future AGI sandbox; voice runs on a Preview provider or replaying
 * production calls.
 */
export function isPreviewRun(run) {
  const kind = runKindOf(run);
  const c = run?.config ?? {};
  if (kind === "harness") return true;
  if (kind === "code")
    return (
      PREVIEW_CODE_MODES.has(c.code?.mode) || c.execution?.mode === "sandbox"
    );
  if (kind === "voice")
    return PREVIEW_VOICE.has(c.voice?.provider) || c.voice?.depth === "replay";
  return false;
}

/**
 * IT-R2 G2 P6: what a run puts out, so a default comparison never pairs a
 * text-only voice run with a full-voice call, or an image-output task with
 * a text task: `voice` (a full or replayed call), `text` (incl. a voice
 * agent run on text), `speech` (a prompt spoken by text to speech),
 * `image` (config.output_modality), else the config's output modality or
 * `text`.
 * @param {object} run
 * @returns {string}
 */
export function outputModalityOf(run) {
  const c = run?.config ?? {};
  if (runKindOf(run) === "voice")
    return (c.voice?.depth ?? run?.target?.depth) === "text_only"
      ? "text"
      : "voice";
  if (run?.target_type === "tts") return "speech";
  return c.output_modality ?? c.prompt?.output_modality ?? "text";
}

/** True when two runs can be a default pair (same output modality). */
export const sameOutput = (a, b) =>
  Boolean(a && b) && outputModalityOf(a) === outputModalityOf(b);

/** IT-R2 G2 P1: from this many runs a runs view opens on the Matrix. */
export const MATRIX_MIN_RUNS = 3;

/**
 * The tab a runs view opens on when no tab is named (R2-SPEC §0.3):
 * `matrix` when the runs are of two or more kinds, or when there are 3+ of
 * them (IT-R2 G2 P1: a 7-run experiment answers on the Matrix, not on 3
 * output columns of the Data tab), else `data`.
 * @param {object[]} runs  run objects (ids alone cannot tell kinds apart)
 * @returns {"matrix"|"data"}
 */
export function defaultRunsTab(runs) {
  const list = (runs ?? []).filter(Boolean);
  if (list.length >= MATRIX_MIN_RUNS) return "matrix";
  const kinds = new Set(list.map(runKindOf));
  return kinds.size >= 2 ? "matrix" : "data";
}

/** Runs whose results carry artifacts (transcript, container): harness, code. */
export function isArtifactsRun(run) {
  if (!run) return false;
  const kind = runKindOf(run);
  return kind === "harness" || kind === "code";
}
