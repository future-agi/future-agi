/*
  The agent families and versions the connection paths are tested against
  (roadmap connect.md, Week 2 stretch): what was tested, the evidence, and
  what isn't covered. Untested means untested — never shown as supported.
*/
export const TESTED_VERSIONS = [
  {
    family: "Python service",
    category: "chat",
    how: "HTTP",
    versions: "FastAPI 0.115 · Flask 3.0",
    status: "Supported",
    evidence: "Reference refund app passed the returns world",
    limits: "Streaming replies not mapped yet",
  },
  {
    family: "Node service",
    category: "chat",
    how: "HTTP",
    versions: "Express 4.21 · Fastify 5.1",
    status: "Supported",
    evidence: "Same seeded task as the Python app",
    limits: "—",
  },
  {
    family: "LangGraph",
    category: "chat",
    how: "Process",
    versions: "langgraph 0.2.x",
    status: "Supported",
    evidence: "Start, turns, tools and cancel checked",
    limits: "Checkpointer memory isn't restored between trials",
  },
  {
    family: "OpenAI Agents SDK",
    category: "chat",
    how: "Process",
    versions: "openai-agents 0.0.x",
    status: "Supported",
    evidence: "Handoffs observed from its trace events",
    limits: "—",
  },
  {
    family: "Claude Agent SDK",
    category: "chat",
    how: "Process",
    versions: "claude-agent-sdk 0.1.x",
    status: "Supported",
    evidence: "Start, turns, tool binding and stop checked",
    limits: "Sub-agent turns are reported inside the parent's",
  },
  {
    family: "Pydantic AI",
    category: "chat",
    how: "Process",
    versions: "pydantic-ai 0.4.x",
    status: "Supported",
    evidence: "Typed tool calls and run results observed from its spans",
    limits: "Streamed structured output isn't mapped yet",
  },
  {
    family: "Google ADK",
    category: "chat",
    how: "Process",
    versions: "google-adk 1.x",
    status: "Partial",
    evidence: "Start, turns, tool binding and sessions",
    limits:
      "Stop isn't honored mid-turn — the runner finishes its current agent",
  },
  {
    family: "Deep Agents",
    category: "multi",
    how: "Process",
    versions: "deepagents 0.0.x",
    status: "Partial",
    evidence: "Planning tool, sub-agent calls and file writes observed",
    limits: "Its virtual filesystem isn't cleared between trials",
  },
  {
    family: "CrewAI",
    category: "multi",
    how: "Process",
    versions: "crewai 0.80 · traceAI-crewai 0.1.10",
    status: "Partial",
    evidence: "Turns and tools",
    limits:
      "Delegation only from crew callbacks; some steps unmeasured. Runs with real credentials need traceAI-crewai 0.1.10 or later",
  },
  {
    family: "Coordinator + two workers",
    category: "multi",
    how: "Process",
    versions: "Reference returns app v1",
    status: "Partial",
    evidence: "Delegation and return events, worker-attributed tools",
    limits: "Sequential, one level; no parallel workers",
  },
  {
    family: "Async task (A2A)",
    category: "artifact",
    how: "Job ID",
    versions: "Reference adapter 0.1",
    status: "Untested",
    evidence: "—",
    limits: "Stretch — not certified yet",
  },
  {
    family: "Coding agent CLI",
    category: "coding",
    how: "Repository task",
    versions: "Claude Code 1.x · Codex CLI",
    status: "Partial",
    evidence: "Patch and verifier output captured",
    limits: "Hidden verifier kept outside its workspace; no resume",
  },
  {
    family: "LiveKit voice",
    category: "voice",
    how: "Audio or text",
    versions: "livekit-agents 0.12",
    status: "Partial",
    evidence: "Text rehearsal and audio session",
    limits: "Interruption timing only in audio mode",
  },
  {
    family: "Vapi",
    category: "platform",
    how: "Platform",
    versions: "API 2025-10",
    status: "Supported",
    evidence: "Test copy, transfers observed",
    limits: "Transfers and end call run for real",
  },
  {
    family: "Retell",
    category: "platform",
    how: "Platform",
    versions: "API v2",
    status: "Supported",
    evidence: "Pinned draft, node changes observed",
    limits: "Unpinned drafts can drift",
  },
  {
    family: "Artifact-producing agent",
    category: "artifact",
    how: "File in, file out",
    versions: "Reference report writer 0.3",
    status: "Partial",
    evidence: "PDF and CSV retrieved and format-checked",
    limits:
      "Only declared formats are graded; a filename alone isn't an artifact",
  },
  {
    family: "Browser agent",
    category: "browser",
    how: "Browser",
    versions: "—",
    status: "Untested",
    evidence: "—",
    limits: "Not in this release",
  },
];

/* Families, in the order the roadmap lists them. */
export const FAMILY_CATEGORIES = [
  { id: "chat", label: "Chat, RAG and tools" },
  { id: "coding", label: "Coding" },
  { id: "browser", label: "Browser" },
  { id: "voice", label: "Voice" },
  { id: "multi", label: "Multi-agent" },
  { id: "artifact", label: "Artifacts and async" },
  { id: "platform", label: "Managed platforms" },
];

const S = "supported";
const P = "partial";
const U = "unavailable";
const T = "untested";
/* Each family's linked diagnostic: per capability, what the reference run showed. */
const DIAG = {
  "Python service": {
    start: S,
    message: S,
    continuity: S,
    streaming: P,
    toolRouting: S,
    state: S,
    cancel: S,
    reset: S,
    cost: P,
  },
  "Node service": {
    start: S,
    message: S,
    continuity: S,
    streaming: P,
    toolRouting: S,
    state: S,
    cancel: S,
    reset: S,
    cost: P,
  },
  LangGraph: {
    start: S,
    message: S,
    continuity: S,
    streaming: S,
    toolRouting: S,
    state: S,
    cancel: S,
    reset: S,
    resume: P,
    cost: P,
  },
  "OpenAI Agents SDK": {
    start: S,
    message: S,
    continuity: S,
    streaming: S,
    toolRouting: S,
    state: S,
    children: S,
    cancel: S,
    reset: S,
    cost: P,
  },
  "Claude Agent SDK": {
    start: S,
    message: S,
    continuity: S,
    streaming: S,
    toolRouting: S,
    state: S,
    children: P,
    cancel: S,
    reset: S,
    cost: P,
  },
  "Pydantic AI": {
    start: S,
    message: S,
    continuity: S,
    streaming: P,
    toolRouting: S,
    state: S,
    cancel: S,
    reset: S,
    cost: P,
  },
  "Google ADK": {
    start: S,
    message: S,
    continuity: S,
    streaming: S,
    toolRouting: S,
    state: S,
    children: P,
    cancel: U,
    reset: S,
    cost: P,
  },
  "Deep Agents": {
    start: S,
    message: S,
    continuity: S,
    toolRouting: S,
    state: S,
    children: S,
    cancel: S,
    reset: P,
    cost: P,
  },
  CrewAI: {
    start: S,
    message: S,
    continuity: S,
    toolRouting: S,
    state: S,
    children: P,
    cancel: P,
    reset: S,
    cost: P,
  },
  "Coordinator + two workers": {
    start: S,
    message: S,
    continuity: S,
    toolRouting: S,
    state: S,
    children: P,
    cancel: S,
    reset: S,
    cost: P,
  },
  "Async task (A2A)": { start: T, message: T, files: T, cancel: T, resume: T },
  "Artifact-producing agent": {
    start: S,
    message: S,
    files: P,
    toolRouting: S,
    state: S,
    cancel: S,
    reset: S,
    cost: P,
  },
  "Coding agent CLI": {
    start: S,
    message: S,
    files: S,
    toolRouting: S,
    state: S,
    cancel: S,
    reset: S,
    resume: U,
    cost: P,
  },
  "LiveKit voice": {
    start: S,
    message: S,
    continuity: S,
    streaming: S,
    audio: P,
    toolRouting: S,
    state: S,
    cancel: S,
    reset: S,
    cost: P,
  },
  Vapi: {
    start: S,
    message: S,
    continuity: S,
    streaming: S,
    audio: S,
    toolRouting: P,
    state: U,
    cancel: S,
    reset: S,
    cost: S,
  },
  Retell: {
    start: S,
    message: S,
    continuity: S,
    streaming: S,
    audio: S,
    toolRouting: P,
    state: U,
    cancel: S,
    reset: S,
    cost: S,
  },
  "Browser agent": { start: T, browser: T, state: T, reset: T },
};

/** A family's diagnostic: { id, at, results: { capability: status } }, or null when untested. */
export const diagnosticOf = (row) => {
  const results = DIAG[row.family] || {};
  if (row.status === "Untested") return { id: null, at: null, results };
  let h = 0;
  for (const c of row.family) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return {
    id: `diag_${h.toString(36).slice(0, 7)}`,
    at: new Date(Date.UTC(2026, 9, 20 + (h % 8), 9 + (h % 8))).toISOString(),
    results,
  };
};

/** The family row this agent belongs to, or null. */
export const familyOf = (agent, kind) => {
  const v = agent?.values || {};
  const f = String(v.framework || "").toLowerCase();
  const p = String(v.provider || "").toLowerCase();
  const find = (name) => TESTED_VERSIONS.find((r) => r.family === name) || null;
  if (p === "vapi") return find("Vapi");
  if (p === "retell") return find("Retell");
  if (/livekit|pipecat/.test(f)) return find("LiveKit voice");
  if (/langgraph/.test(f)) return find("LangGraph");
  if (/openai/.test(f)) return find("OpenAI Agents SDK");
  if (/claude|anthropic/.test(f)) return find("Claude Agent SDK");
  if (/pydantic/.test(f)) return find("Pydantic AI");
  if (/adk|google/.test(f)) return find("Google ADK");
  if (/deep\s?agents?/.test(f)) return find("Deep Agents");
  if (/crew/.test(f)) return find("CrewAI");
  if (v.connection?.method === "async") return find("Async task (A2A)");
  if (kind === "repo" || kind === "upload" || kind === "endpoint")
    return find(
      v.language === "TypeScript" || /node|express/.test(f)
        ? "Node service"
        : "Python service",
    );
  return null;
};

/*
  The qualification matrix (connect.md CN-4): every family gets the same
  checks, all at once. A failure is that family's result — it never stops or
  hides the others. Outcomes come from each family's reference diagnostic:
  supported passes, partial passes with a limit, unavailable fails. A family
  with no reference agent yet isn't run.
*/
/* T43: every framework gets the same checks — start, a two-turn session,
   tool binding, evidence and stop. */
export const QUAL_CHECKS = [
  "start",
  "continuity",
  "toolRouting",
  "trace",
  "cancel",
];
export const QUAL_LABEL = {
  start: "Start",
  continuity: "Two-turn session",
  toolRouting: "Tool binding",
  trace: "Evidence",
  cancel: "Stop",
};

/*
  SSE and custom WebSocket transports stay unsupported until an adapter for
  them passes qualification (connect.md, T43). A family declares the ones it
  has an adapter for in `transports`; none does yet, and only a family whose
  qualification passed counts.
*/
export const transportQualified = (row, method) =>
  !!row &&
  (row.transports || []).includes(method) &&
  qualificationOf(row).outcome === "passed";

export const qualificationOf = (row) => {
  if (row.status === "Untested")
    return { outcome: "not-run", checks: [], failed: [], limited: [] };
  const results = DIAG[row.family] || {};
  /* Evidence is the trace a family emits, else the state we read back. */
  const checks = QUAL_CHECKS.map((id) => ({
    id,
    result: results[id] || (id === "trace" ? results.state : null) || T,
  }));
  const failed = checks
    .filter((c) => c.result === U || c.result === T)
    .map((c) => c.id);
  const limited = checks.filter((c) => c.result === P).map((c) => c.id);
  return {
    outcome: failed.length ? "failed" : limited.length ? "limited" : "passed",
    checks,
    failed,
    limited,
  };
};

/* How long a family's checks take in the prototype — they finish at different times. */
export const qualificationMs = (row) => {
  let h = 0;
  for (const c of row.family) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return 1200 + (h % 9) * 350;
};
