/**
 * Two shapes land in runs.json: the live-call record (settled/judged/calls) and the older
 * local-run record (checkpoints/transcript/actions). Both deserve their whole story shown, so
 * they are reconciled into one before anything tries to draw them.
 */
export const normalizeRun = (run) => {
  if (run.settled) {
    return {
      scenario: run.scenario,
      passed: run.passed,
      met: run.met,
      of: run.of,
      checks: (run.settled || []).map((one) => ({
        name: one.name,
        passed: one.held,
        why: one.said,
        broken: one.broken,
        kind: "code",
      })),
      judged: run.judged || [],
      calls: run.calls || [],
      problems: run.problems || [],
      transcript: run.transcript || "",
      instruction: run.instruction || "",
      live: true,
    };
  }
  return {
    scenario: run.scenario,
    passed: run.passed,
    met: run.met !== undefined ? run.met : (run.checkpoints || []).filter((one) => one.passed).length,
    of: run.of !== undefined ? run.of : (run.checkpoints || []).length,
    checks: (run.checkpoints || []).map((check) => ({
      name: check.name,
      passed: check.passed,
      why: check.detail,
      kind: check.kind || "",
      by: check.by || "",
    })),
    judged: [],
    calls: [],
    problems: [],
    transcript: run.transcript || "",
    actions: run.actions || "",
    turns: run.turns,
    ended: run.ended,
    live: false,
  };
};

export default normalizeRun;
