import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { buildRun } from "../_mock/runStream";

/**
 * Plays a built run back on a timer.
 *
 * Tasks advance independently across N workers, which is what produces the
 * staggered, genuinely-concurrent feel of a real batch — a single global tick
 * would make every task move in lockstep and look fake.
 *
 * Speed is adjustable so a demo can be slowed down on an interesting task or
 * run out fast. There is deliberately no pause: the timeline is a set of
 * scheduled timers, so a pause control would have to unwind and reschedule all
 * of them — a half-working pause is worse than none.
 */
export default function useRunPlayer({ seed, scenarios, stage, evals, tools = [], concurrency = 4, repeats = 3, phrasing = 0 }) {
  const run = useMemo(
    () => buildRun({ seed, scenarios, stage, evals, tools, concurrency, repeats, phrasing }),
    [seed, scenarios, stage, evals, tools, concurrency, repeats, phrasing],
  );

  const [phase, setPhase] = useState("booting"); // booting | running | done
  /*
    When anything last actually moved.

    A long stage and a dead one look identical on a progress bar, and the
    difference is the whole question a user has while waiting. So the last
    state change is timestamped, and the view reports how long ago it was
    rather than implying health from the fact that a spinner is spinning.
  */
  const [lastEventAt, setLastEventAt] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  const [speed, setSpeed] = useState(1);
  const [tasks, setTasks] = useState(() =>
    run.tasks.map((t) => ({ ...t, status: "queued", stepIndex: -1, evalIndex: -1 })),
  );
  const [focusId, setFocusId] = useState(run.tasks[0]?.id || null);
  const [elapsed, setElapsed] = useState(0);

  const timers = useRef([]);
  const clearTimers = () => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
  };

  // Reset whenever the underlying run changes.
  useEffect(() => {
    setTasks(run.tasks.map((t) => ({ ...t, status: "queued", stepIndex: -1, evalIndex: -1 })));
    setPhase("booting");
    setElapsed(0);
    setFocusId(run.tasks[0]?.id || null);
    return clearTimers;
  }, [run]);

  const patchTask = useCallback((id, patch) => {
    setTasks((ts) => ts.map((t) => (t.id === id ? { ...t, ...patch } : t)));
    setLastEventAt(Date.now());
  }, []);

  /* One ticker for the whole view; the heartbeat is derived from it. */
  useEffect(() => {
    if (phase === "done") return undefined;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [phase]);

  /** Drives one task through its steps, then grading, then a verdict. */
  const driveTask = useCallback(
    (task, startDelay) => {
      const push = (fn, ms) => {
        timers.current.push(setTimeout(fn, ms));
      };
      let t = startDelay;

      push(() => patchTask(task.id, { status: "running", stepIndex: 0 }), t);

      task.steps.forEach((step, i) => {
        t += step.duration / speed;
        push(() => {
          // A failing task stops progressing at its failure point.
          if (task.failStep != null && i > task.failStep) return;
          patchTask(task.id, { stepIndex: i });
        }, t);
      });

      t += 400 / speed;
      push(() => patchTask(task.id, { status: "grading" }), t);

      task.evalResults.forEach((_, i) => {
        t += 320 / speed;
        push(() => patchTask(task.id, { evalIndex: i }), t);
      });

      t += 300 / speed;
      push(
        () => patchTask(task.id, { status: task.verdict, evalIndex: task.evalResults.length }),
        t,
      );

      return t;
    },
    [patchTask, speed],
  );

  const start = useCallback(() => {
    clearTimers();
    setPhase("running");

    // Each worker lane runs its tasks back to back; lanes run in parallel.
    const lanes = {};
    run.tasks.forEach((task) => {
      const lane = task.worker;
      const startAt = (lanes[lane] || 0) + Math.random() * 200;
      lanes[lane] = driveTask(task, startAt);
    });

    const total = Math.max(...Object.values(lanes), 1000);
    timers.current.push(setTimeout(() => setPhase("done"), total + 500));
  }, [run.tasks, driveTask]);

  // Elapsed clock.
  useEffect(() => {
    if (phase !== "running") return undefined;
    const id = setInterval(() => setElapsed((e) => e + 100 * speed), 100);
    return () => clearInterval(id);
  }, [phase, speed]);

  useEffect(() => clearTimers, []);

  const focus = tasks.find((t) => t.id === focusId) || tasks[0];

  const stats = useMemo(() => {
    const FINISHED = ["passed", "failed", "flaky", "unmeasured"];
    const done = tasks.filter((t) => FINISHED.includes(t.status));
    const passed = tasks.filter((t) => t.status === "passed").length;
    const failed = tasks.filter((t) => t.status === "failed").length;
    /* Scenarios whose samples disagreed with each other. Counted apart from
       both — calling them failures blames the agent for the scenario. */
    const flaky = tasks.filter((t) => t.status === "flaky").length;
    /* Scenarios nothing could be measured on — ours to fix, not the agent's. */
    const unmeasured = tasks.filter((t) => t.status === "unmeasured").length;
    const measured = done.filter((t) => t.status !== "unmeasured");
    const active = tasks.filter((t) => ["running", "grading"].includes(t.status)).length;
    return {
      total: tasks.length,
      done: done.length,
      passed,
      failed,
      flaky,
      unmeasured,
      measured: measured.length,
      repeats: run.repeats || 1,
      active,
      queued: tasks.length - done.length - active,
      /* The mean pass proportion, so a scenario that passed two of three
         samples counts as two thirds rather than as a clean pass or a loss. */
      /* Over what was measurable, never over what was attempted. */
      passRate: measured.length
        ? measured.reduce((a, t) => a + (t.passShare ?? (t.status === "passed" ? 1 : 0)), 0) / measured.length
        : 0,
      progress: tasks.length ? (done.length / tasks.length) * 100 : 0,
      cost: done.reduce((a, t) => a + t.cost, 0),
      tokens: done.reduce((a, t) => a + t.tokens, 0),
    };
  }, [tasks, run.repeats]);

  return {
    phase, setPhase, start,
    tasks, focus, focusId, setFocusId,
    stats, elapsed,
    /* Seconds since anything last changed, and whether that is long enough to
       stop calling it slow. */
    sinceLastEvent: Math.max(0, Math.round((now - lastEventAt) / 1000)),
    stalled: phase === "running" && now - lastEventAt > 15000,
    speed, setSpeed,
    stage: run.stage,
  };
}
