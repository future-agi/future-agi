/*
  One import review for every kind of evidence (clarified plan, evidence.md):
  Observe traces, an uploaded transcript, a CSV/JSON dataset and a failed
  simulation all become the same record the trace import already reviews —
  { id, traceId, at, channel, outcome, topic, callerName, turns, evidence,
  sourceKind } — so they go through one pick → freeze → review path. Each
  keeps its origin; nothing is relabelled as a production trace.
*/
import { rebuildRun } from "./comparison";

export const EVIDENCE_KINDS = {
  observe: { label: "Observe traces", noun: "trace" },
  transcript: { label: "Transcript", noun: "transcript" },
  dataset: { label: "CSV / JSON", noun: "row" },
  simulation: { label: "Failed simulation", noun: "trial" },
};

const ROLE = /^(caller|customer|user|client|human)\s*:/i;
const AGENT = /^(agent|assistant|bot|ai)\s*:/i;

/** A pasted or uploaded transcript: "Customer: …" / "Agent: …" lines. */
export const parseTranscript = (text, name, n = 1) => {
  const turns = String(text || "")
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) =>
      ROLE.test(l)
        ? { role: "caller", text: l.replace(ROLE, "").trim() }
        : AGENT.test(l)
          ? { role: "agent", text: l.replace(AGENT, "").trim() }
          : null,
    )
    .filter(Boolean);
  if (!turns.some((t) => t.role === "caller")) return null;
  return {
    id: `up_${Date.now().toString(36)}_${n}`,
    traceId: `UP-${n}`,
    at: new Date().toISOString(),
    channel: "Transcript",
    outcome: "unknown",
    topic: `From ${name}`,
    callerName: "[customer]",
    turns,
    evidence: { transcript: true, tools: false, startingState: false },
    sourceKind: "transcript",
    sourceName: name,
  };
};

const fieldOf = (row, keys) => {
  const k = Object.keys(row).find((x) => keys.includes(x.toLowerCase()));
  return k ? String(row[k] ?? "") : "";
};

/** CSV or JSON rows: a prompt (and an expected behaviour) each — a row isn't a whole conversation. */
export const parseDataset = (text, name) => {
  let rows = [];
  const raw = String(text || "").trim();
  if (raw.startsWith("[") || raw.startsWith("{")) {
    try {
      const j = JSON.parse(raw);
      rows = Array.isArray(j) ? j : j.rows || [];
    } catch {
      rows = [];
    }
  } else {
    const [head, ...lines] = raw.split(/\r?\n/).filter(Boolean);
    const cols = (head || "").split(",").map((c) => c.trim());
    rows = lines.map((l) => {
      const cells = l.split(",");
      return Object.fromEntries(cols.map((c, i) => [c, (cells[i] || "").trim()]));
    });
  }
  return rows
    .map((r, i) => {
      const prompt = fieldOf(r, ["prompt", "input", "question", "message", "user"]);
      if (!prompt) return null;
      return {
        id: `ds_${Date.now().toString(36)}_${i}`,
        traceId: `${name}#${i + 1}`,
        at: new Date().toISOString(),
        channel: "Dataset row",
        outcome: "unknown",
        topic: prompt.length > 60 ? `${prompt.slice(0, 57)}…` : prompt,
        callerName: "[customer]",
        turns: [{ role: "caller", text: prompt }],
        expected: fieldOf(r, ["expected", "expected_behavior", "expected_behaviour", "answer", "output"]) || null,
        evidence: { transcript: false, tools: false, startingState: false },
        sourceKind: "dataset",
        sourceName: name,
      };
    })
    .filter(Boolean);
};

/** Failed trials of the latest finished run: the simulation recorded the starting state exactly. */
export const failedSimulationsOf = (env, envState) => {
  const run = [...(envState?.runs || [])]
    .filter((r) => r.finishedAt && !r.pending && (r.failed || 0) > 0)
    .sort((a, b) => String(b.finishedAt).localeCompare(String(a.finishedAt)))[0];
  if (!run) return [];
  let tasks = [];
  try {
    tasks = rebuildRun(env, envState, run) || [];
  } catch {
    return [];
  }
  const byId = new Map((envState?.scenarios || []).map((s) => [s.id, s]));
  const seen = new Set();
  return tasks
    .filter((t) => t.status === "failed" && !seen.has(t.scenarioId) && seen.add(t.scenarioId))
    .slice(0, 12)
    .map((t, i) => {
      const sc = byId.get(t.scenarioId) || {};
      return {
        id: `sim_${run.id}_${t.scenarioId}`,
        traceId: `Run ${run.ordinal} · ${i + 1}`,
        at: run.finishedAt,
        channel: "Simulation",
        outcome: "failed",
        topic: sc.title || t.title || "Failed trial",
        callerName: sc.persona?.name || "[customer]",
        turns: [{ role: "caller", text: sc.situation || sc.task || sc.title || "" }],
        agentVersion: run.agentVersion,
        evidence: { transcript: true, tools: true, startingState: true },
        sourceKind: "simulation",
        sourceName: `Run ${run.ordinal}`,
      };
    });
};
