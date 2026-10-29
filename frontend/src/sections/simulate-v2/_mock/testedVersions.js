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
    family: "CrewAI",
    category: "multi",
    how: "Process",
    versions: "crewai 0.80",
    status: "Partial",
    evidence: "Turns and tools",
    limits: "Delegation only from crew callbacks; some steps unmeasured",
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
    limits: "Only declared formats are graded; a filename alone isn't an artifact",
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
  "Python service": { start: S, message: S, continuity: S, streaming: P, toolRouting: S, state: S, cancel: S, reset: S, cost: P },
  "Node service": { start: S, message: S, continuity: S, streaming: P, toolRouting: S, state: S, cancel: S, reset: S, cost: P },
  LangGraph: { start: S, message: S, continuity: S, streaming: S, toolRouting: S, state: S, cancel: S, reset: S, resume: P, cost: P },
  "OpenAI Agents SDK": { start: S, message: S, continuity: S, streaming: S, toolRouting: S, state: S, children: S, cancel: S, reset: S, cost: P },
  CrewAI: { start: S, message: S, continuity: S, toolRouting: S, state: S, children: P, cancel: P, reset: S, cost: P },
  "Coordinator + two workers": { start: S, message: S, continuity: S, toolRouting: S, state: S, children: P, cancel: S, reset: S, cost: P },
  "Async task (A2A)": { start: T, message: T, files: T, cancel: T, resume: T },
  "Artifact-producing agent": { start: S, message: S, files: P, toolRouting: S, state: S, cancel: S, reset: S, cost: P },
  "Coding agent CLI": { start: S, message: S, files: S, toolRouting: S, state: S, cancel: S, reset: S, resume: U, cost: P },
  "LiveKit voice": { start: S, message: S, continuity: S, streaming: S, audio: P, toolRouting: S, state: S, cancel: S, reset: S, cost: P },
  Vapi: { start: S, message: S, continuity: S, streaming: S, audio: S, toolRouting: P, state: U, cancel: S, reset: S, cost: S },
  Retell: { start: S, message: S, continuity: S, streaming: S, audio: S, toolRouting: P, state: U, cancel: S, reset: S, cost: S },
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
  if (/crew/.test(f)) return find("CrewAI");
  if (v.connection?.method === "async") return find("Async task (A2A)");
  if (kind === "repo" || kind === "upload" || kind === "endpoint")
    return find(v.language === "TypeScript" || /node|express/.test(f) ? "Node service" : "Python service");
  return null;
};
