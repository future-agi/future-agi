/*
  Run readiness (plan T06; runs.md "Review readiness"; questions.md CH-9):
  a short Ready to run panel in Run setup, not a report or a score. For the
  selected agent version and environment it answers six things — does the
  agent start and reply, are its tools wired to the test world, does the
  world reset, can the run be stopped, can we capture the evidence the
  checks need, and can every scenario be graded. Each blocker names the
  scenarios it affects and a fix. No cost estimate.

  It reads the same per-capability checks the connection check fills in.
*/
import { COMPAT_STATUS } from "./compatibility";

export const READINESS_AREAS = [
  {
    id: "start",
    label: "Starts and replies",
    members: ["start", "message", "continuity"],
  },
  {
    id: "tools",
    label: "Tools wired to the test world",
    members: ["toolSchema", "toolsNeeded", "toolRouting"],
  },
  { id: "reset", label: "World resets", members: ["reset"] },
  { id: "cancel", label: "Run can be stopped", members: ["cancel"] },
  {
    id: "evidence",
    label: "Evidence the checks need",
    members: [
      "state",
      "trace",
      "audio",
      "browser",
      "transfer",
      "children",
      "files",
    ],
  },
  { id: "grading", label: "Grading coverage", members: [] },
];

/** Ready · Blocked · Partial · Not checked, as a person reads them. */
export const READY_STATUS = {
  ready: { label: "Ready", dot: "passed" },
  partial: { label: "Partial", dot: "flaky" },
  unchecked: { label: "Not checked", dot: "queued" },
  blocked: { label: "Blocked", dot: "failed" },
};

const areaStatusOf = (rows) => {
  if (rows.some((r) => r.status === "blocked" || r.status === "deprecated"))
    return "blocked";
  if (rows.some((r) => r.status === "untested" || r.status === "stale"))
    return "unchecked";
  /* Unavailable evidence only matters when a case needs it. */
  if (
    rows.some(
      (r) =>
        r.status === "partial" ||
        (r.status === "unavailable" && (r.affected || []).length),
    )
  )
    return "partial";
  return "ready";
};

/**
 * The six areas for a report: [{ id, label, status, detail, affected: [ids],
 * fixRow }]. `scenarios` decides grading coverage: a scenario with no
 * sub-goal check and no evaluation can't be graded.
 */
export const readinessOf = (report, scenarios = [], evalCount = 0) => {
  const byId = Object.fromEntries((report?.rows || []).map((r) => [r.id, r]));
  return READINESS_AREAS.map((a) => {
    if (a.id === "grading") {
      const ungraded = evalCount
        ? []
        : scenarios.filter((s) => !(s.subgoals || s.subGoals || []).length);
      return {
        ...a,
        status: ungraded.length ? "partial" : "ready",
        detail: ungraded.length
          ? `${ungraded.length} scenario${ungraded.length === 1 ? " has" : "s have"} no check that can grade it.`
          : "Every scenario has a check that can grade it.",
        affected: ungraded.map((s) => s.id),
        fixRow: null,
      };
    }
    const rows = a.members.map((m) => byId[m]).filter(Boolean);
    const status = rows.length ? areaStatusOf(rows) : "ready";
    const worst = [...rows].sort(
      (x, y) =>
        (COMPAT_STATUS[y.status]?.rank || 0) -
        (COMPAT_STATUS[x.status]?.rank || 0),
    )[0];
    const affected = [
      ...new Set(
        rows
          .filter((r) => r.status !== "supported")
          .flatMap((r) => (Array.isArray(r.affected) ? r.affected : [])),
      ),
    ];
    return {
      ...a,
      status,
      detail:
        status === "ready"
          ? rows.length
            ? `${rows.map((r) => r.label).join(", ")} checked.`
            : "Nothing here needs it."
          : status === "unchecked"
            ? "Not checked for this agent yet — run the check."
            : worst?.detail || "",
      affected,
      fixRow: status === "blocked" || status === "partial" ? worst : null,
    };
  });
};
