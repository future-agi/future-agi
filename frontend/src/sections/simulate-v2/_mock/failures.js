/**
 * Whose fault it was.
 *
 * A run produces failures from five different places, and only one of them is
 * evidence about the agent. The other four are noise — and noise recorded as
 * agent failure is worse than no measurement at all, because it looks like
 * data. Somebody reads 43%, changes a prompt, and the number moves because a
 * container came up that time.
 *
 * So every failed scenario is attributed to a domain before it is counted, and
 * the attribution decides three separate things:
 *
 *   what is reported   only agent failures belong in the agent's denominator
 *   what is retried    infrastructure may be retried; an agent failure may not
 *   what happens next  prompt change, environment fix, scenario fix, grader fix
 *
 * Agent failure is the residual: what is left when the environment came up, the
 * call connected, the simulated caller behaved and the grader returned. That
 * ordering is the whole trust argument — "your agent failed" is only credible
 * when everything else can be shown to have worked.
 */

export const DOMAINS = {
  /*
    The only domain that is data. A failure here is an agent-learning failure:
    it is the point of running at all, and it must never be retried away.
  */
  agent: {
    id: "agent",
    label: "Agent behaviour",
    short: "Agent",
    color: "#C2603F",
    measured: true,
    blurb: "The world was up, the call connected and the caller behaved — the agent did the wrong thing.",
    retry: "Recorded. Never retried — retrying until it passes would manufacture the result.",
    next: "Change the agent: prompt, tools, or the policy it follows.",
  },
  environment: {
    id: "environment",
    label: "Environment",
    short: "Environment",
    color: "#CA8A04",
    measured: false,
    blurb: "The environment never reached a state the scenario could run against.",
    retry: "Fails with diagnostics. Retried only for operations known to be transient.",
    next: "Fix the environment or its seed data, then run the scenario again.",
  },
  transport: {
    id: "transport",
    label: "Transport",
    short: "Transport",
    color: "#2563EB",
    measured: false,
    blurb: "The session never reached the agent — it did not connect, or it dropped mid-run.",
    retry: "Bounded retry with a new call identity, so a half-finished call is never counted twice.",
    next: "Nothing to change in the agent. Re-run the affected scenarios.",
  },
  simulator: {
    id: "simulator",
    label: "Simulated caller",
    short: "Simulator",
    color: "#7857FC",
    measured: false,
    blurb: "The simulated caller broke its own scenario — contradicted a fact, looped, or refused something the scenario required.",
    retry: "Retried within the simulator policy. A caller that will not follow the scenario is not a test.",
    next: "Fix the scenario's simulator policy, not the agent.",
  },
  grading: {
    id: "grading",
    label: "Grading",
    short: "Grading",
    color: "#0D9488",
    measured: false,
    blurb: "The evidence was captured, but no verdict came back — the grader failed or the check was invalid.",
    retry: "Re-graded from the recorded evidence. The calls are not made again.",
    next: "Fix the check or re-grade — the run itself is intact.",
  },
};

export const DOMAIN_LIST = Object.values(DOMAINS);

/** Domains that leave a scenario without a usable verdict. */
export const UNMEASURED = DOMAIN_LIST.filter((d) => !d.measured).map((d) => d.id);

/**
 * The decision order.
 *
 * Read top to bottom: each step asks whether something other than the agent
 * failed first. Only when all of them held is the failure the agent's, which is
 * why `agent` is last and unconditional.
 */
export const attribute = (task) => {
  if (!task) return null;
  if (task.fault?.environment) return DOMAINS.environment;
  if (task.fault?.transport) return DOMAINS.transport;
  if (task.fault?.simulator) return DOMAINS.simulator;
  if (task.fault?.grading) return DOMAINS.grading;
  return DOMAINS.agent;
};

/** A scenario has a verdict only when nothing upstream of the agent broke. */
export const isMeasured = (task) => !task || attribute(task)?.measured !== false;

/**
 * What actually went wrong, in one line, named by domain.
 *
 * Written from the run's own evidence so it can be checked: a missing probe, a
 * dropped session, a caller that answered a question it had already answered.
 */
export const faultReason = (task) => {
  const f = task?.fault || {};
  if (f.environment) return f.environment;
  if (f.transport) return f.transport;
  if (f.simulator) return f.simulator;
  if (f.grading) return f.grading;
  return null;
};

/** Counts per domain across a set of tasks — the run-health row. */
export const domainTally = (tasks = []) => {
  const held = {};
  tasks.forEach((t) => {
    if (t.status === "passed") return;
    const d = attribute(t);
    if (!d) return;
    (held[d.id] = held[d.id] || []).push(t);
  });
  /* The tasks travel with the count. A tally that only reports "3" makes the
     reader go and find which three; every surface that shows this wants to link
     straight to them. */
  return DOMAIN_LIST
    .map((d) => ({ domain: d, count: (held[d.id] || []).length, tasks: held[d.id] || [] }))
    .filter((r) => r.count > 0);
};

/**
 * Flakiness has two sources and they belong to different people.
 *
 * The agent deciding differently on the same input is a finding about the
 * agent. The simulated caller behaving differently is a finding about the
 * scenario. Reporting both as "flaky" hands the second one to the wrong team.
 */
export const flakySource = (task) => (task?.fault?.simulatorDrift ? "simulator" : "agent");
