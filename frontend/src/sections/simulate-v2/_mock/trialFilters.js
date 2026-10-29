/*
  Live-run filters (roadmap runs.md "Live run and trial detail"): by
  scenario, status, failure domain and actor. A failure domain keeps the
  agent's failures apart from the environment's, the infrastructure's and
  the ones without enough evidence.
*/
import { causeOf, outcomeOfCause } from "./decoupled";

export const STATUS_FILTERS = [
  { id: "queued", label: "Queued" },
  { id: "running", label: "Running" },
  { id: "waiting", label: "Waiting for approval" },
  { id: "done", label: "Finished" },
];

export const DOMAIN_FILTERS = [
  { id: "agent", label: "Agent failed" },
  { id: "environment", label: "Test invalid" },
  { id: "infrastructure", label: "Infrastructure error" },
  { id: "evidence", label: "Not enough evidence" },
];

const DONE = ["passed", "failed", "flaky", "unmeasured"];

export const trialStatusOf = (task, waiting) => {
  if (waiting) return "waiting";
  if (DONE.includes(task.status)) return "done";
  if (task.status === "running" || task.status === "grading") return "running";
  return "queued";
};

export const trialDomainOf = (task) => {
  if (["failed", "flaky"].includes(task.status)) return "agent";
  if (task.status !== "unmeasured") return null;
  return { "test-invalid": "environment", error: "infrastructure" }[outcomeOfCause(causeOf(task))] || "evidence";
};

export const trialActorOf = (task) => (task.steps || []).find((s) => s.role === "actor")?.actorName || null;

/** The actors any trial in the run meets — the actor filter's options. */
export const actorsInRun = (tasks) => [...new Set(tasks.map(trialActorOf).filter(Boolean))];

/** Does `task` pass the filters `{ q, status, domain, actor }`? `waiting` — it's paused on an approval. */
export const matchesTrialFilters = (task, f, waiting = false) => {
  const q = String(f.q || "").trim().toLowerCase();
  if (q && !`${task.title || ""} ${task.name || ""} ${task.task || ""}`.toLowerCase().includes(q)) return false;
  if (f.status && trialStatusOf(task, waiting) !== f.status) return false;
  if (f.domain && trialDomainOf(task) !== f.domain) return false;
  if (f.actor && (f.actor === "none" ? trialActorOf(task) : trialActorOf(task) !== f.actor)) return false;
  return true;
};

export const activeFilterCount = (f) => ["q", "status", "domain", "actor"].filter((k) => f[k]).length;
