import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { buildRun } from "../_mock/runStream";
import { clarifyPlanOf } from "../_mock/clarify";
import { causeOf, outcomeOfCause } from "../_mock/decoupled";

const EMPTY = [];

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
export default function useRunPlayer({
  seed,
  scenarios,
  stage,
  evals,
  tools = [],
  agentTools = null,
  failRate,
  concurrency = 4,
  repeats = 3,
  phrasing = 0,
  /* The run's cast and world — without them no actor ever joined a live run,
     and the live verdicts could differ from the same run read back later. */
  actors = EMPTY,
  envVersion = null,
  toolFaults = null,
  /* Which generation of admission gates judges this run — pinned, so a
     finished run isn't re-judged by gates added later. */
  admissionGates = 3,
  /* The updated RSI PRD's run rules the run pinned (0: none — see runInputs). */
  outcomes = 0,
  requiredEvals = 0,
  /* Sub-goals deciding and the turn limit, as the run pinned them (0: none). */
  gradingRule = 0,
  turnLimits = 0,
  turnLimit = undefined,
  /* Outdated / Re-check scenarios as the run started ({ id: state }) — they
     run, but count toward no decision. */
  staleIds = null,
  /* Whether this run may stop a scenario on a clarifying question (AC-15.13).
     Only a live run ever asks; one read back from history never plays. */
  clarify = true,
  /* The run plan's spending limit: { cap, alertAt, creditsOf(task, {high}) }.
     No limit: nothing is held back. */
  budget = null,
}) {
  const run = useMemo(
    () =>
      buildRun({
        seed,
        scenarios,
        stage,
        evals,
        tools,
        agentTools,
        failRate,
        concurrency,
        repeats,
        phrasing,
        actors,
        envVersion,
        toolFaults,
        admissionGates,
        outcomes,
        requiredEvals,
        gradingRule,
        turnLimits,
        ...(turnLimit != null && { turnLimit }),
      }),
    [
      seed,
      scenarios,
      stage,
      evals,
      tools,
      agentTools,
      failRate,
      concurrency,
      repeats,
      phrasing,
      actors,
      envVersion,
      toolFaults,
      admissionGates,
      outcomes,
      requiredEvals,
      gradingRule,
      turnLimits,
      turnLimit,
    ],
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
    run.tasks.map((t) => ({
      ...t,
      status: "queued",
      stepIndex: -1,
      evalIndex: -1,
    })),
  );
  const [focusId, setFocusId] = useState(run.tasks[0]?.id || null);
  const [elapsed, setElapsed] = useState(0);

  /*
    The one clarifying question this run asks, if any — fixed by the run's
    seed. While it's open, that scenario holds at the turn it reached; the
    other scenarios carry on, and the run can't finish until it's answered
    (or the run is stopped).
  */
  const plan = useMemo(
    () =>
      clarify ? clarifyPlanOf({ seed, tasks: run.tasks, concurrency }) : null,
    [clarify, seed, run, concurrency],
  );
  const [blocked, setBlocked] = useState(null);
  const [clarifications, setClarifications] = useState([]);
  const holdRef = useRef(false);
  /*
    The spending limit. Credits are counted when a scenario finishes; a
    running one holds the most it can use. A scenario starts only if what's
    used plus what's running plus its own most stays under the limit — at the
    limit no new scenarios start, and the ones running finish.
  */
  const budgetRef = useRef(budget);
  /* The latest budget each render — what's known about the agent (its
     voice platform) can arrive after the first render. The limit itself
     lives in capRef, so a later render never resets a lowered limit. */
  if (budget) budgetRef.current = budget;
  const capRef = useRef(budget?.cap || null);
  /* Calls admitted so far — what a provider's own limit counts. */
  const admittedRef = useRef(0);
  const spentRef = useRef(0);
  const inflightRef = useRef(new Map());
  const skippedRef = useRef(new Set());
  const stopRef = useRef(null);
  const alertedRef = useRef(false);
  const [spend, setSpend] = useState({
    used: 0,
    running: 0,
    cap: budget?.cap || null,
    reached: null,
    alert: false,
  });
  const syncSpend = (patch = {}) =>
    setSpend((s) => ({
      ...s,
      used: Math.round(spentRef.current),
      running: Math.round(
        [...inflightRef.current.values()].reduce((a, b) => a + b, 0),
      ),
      cap: capRef.current,
      ...patch,
    }));
  const doneWaitingRef = useRef(false);
  const resumeRef = useRef(null);
  const driveRef = useRef(null);

  const timers = useRef([]);
  const clearTimers = () => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
  };
  const clearHold = () => {
    holdRef.current = false;
    doneWaitingRef.current = false;
    resumeRef.current = null;
    setBlocked(null);
  };

  // Reset whenever the underlying run changes.
  useEffect(() => {
    spentRef.current = 0;
    admittedRef.current = 0;
    inflightRef.current = new Map();
    skippedRef.current = new Set();
    stopRef.current = null;
    alertedRef.current = false;
    capRef.current = budgetRef.current?.cap || null;
    setSpend({
      used: 0,
      running: 0,
      cap: capRef.current,
      reached: null,
      alert: false,
    });
    setTasks(
      run.tasks.map((t) => ({
        ...t,
        status: "queued",
        stepIndex: -1,
        evalIndex: -1,
      })),
    );
    setPhase("booting");
    setElapsed(0);
    setFocusId(run.tasks[0]?.id || null);
    clearHold();
    setClarifications([]);
    return clearTimers;
  }, [run]);

  const patchTask = useCallback((id, patch) => {
    setTasks((ts) => ts.map((t) => (t.id === id ? { ...t, ...patch } : t)));
    setLastEventAt(Date.now());
  }, []);

  /* One ticker for the whole view; the heartbeat is derived from it. Only
     while a run is actually running — a run opened from history never leaves
     "booting", and ticking there re-rendered the whole run page (Analytics
     and all) every second for as long as it was open. */
  useEffect(() => {
    if (phase !== "running") return undefined;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [phase]);

  /**
   * Drives one task through its steps, then grading, then a verdict.
   * `from` resumes a held task at the turn after it stopped; `blockAt` holds
   * it at that turn on the run's clarifying question and frees its lane.
   */
  const driveTask = useCallback(
    (task, startDelay, { from = 0, blockAt = null } = {}) => {
      /* A scenario the limit kept from starting plays nothing further. */
      const push = (fn, ms) => {
        timers.current.push(
          setTimeout(() => {
            if (skippedRef.current.has(task.id)) return;
            fn();
          }, ms),
        );
      };
      let t = startDelay;

      if (from === 0)
        push(() => {
          const b = budgetRef.current;
          if (b && capRef.current) {
            const most = b.creditsOf(task, { high: true });
            const running = [...inflightRef.current.values()].reduce(
              (a, v) => a + v,
              0,
            );
            /* The run's limit, or the month's credits left if that's lower
               — low account credit holds queued work the same way. */
            /* A voice platform's own limit (the customer's Vapi or Retell
               account) stops new calls the same way, measured in calls. */
            const providerFull =
              b.providerCalls != null && admittedRef.current >= b.providerCalls;
            const ceiling = Math.min(capRef.current, b.accountLeft ?? Infinity);
            if (
              stopRef.current ||
              providerFull ||
              spentRef.current + running + most > ceiling
            ) {
              if (!stopRef.current)
                stopRef.current = providerFull
                  ? "provider"
                  : ceiling < capRef.current
                    ? "credits"
                    : "limit";
              holdQueued();
              syncSpend({ reached: stopRef.current });
              return;
            }
            admittedRef.current += 1;
            inflightRef.current.set(task.id, most);
            syncSpend();
          }
          patchTask(task.id, { status: "running", stepIndex: 0 });
        }, t);

      for (let i = from; i < task.steps.length; i += 1) {
        const at = i;
        t += task.steps[i].duration / speed;
        push(() => {
          // A failing task stops progressing at its failure point.
          if (task.failStep != null && at > task.failStep) return;
          patchTask(task.id, { stepIndex: at });
        }, t);
        if (blockAt != null && at === blockAt) {
          push(() => {
            holdRef.current = true;
            resumeRef.current = () =>
              driveRef.current?.(task, 0, { from: at + 1 });
            patchTask(task.id, { blocked: true });
            setBlocked({ ...plan, askedAt: Date.now() });
          }, t + 1);
          return t;
        }
      }

      t += 400 / speed;
      push(() => patchTask(task.id, { status: "grading" }), t);

      task.evalResults.forEach((_, i) => {
        t += 320 / speed;
        push(() => patchTask(task.id, { evalIndex: i }), t);
      });

      t += 300 / speed;
      push(() => {
        patchTask(task.id, {
          status: task.verdict,
          evalIndex: task.evalResults.length,
        });
        const b = budgetRef.current;
        if (!b || !inflightRef.current.has(task.id)) return;
        inflightRef.current.delete(task.id);
        spentRef.current += b.creditsOf({ ...task, status: task.verdict });
        const running = [...inflightRef.current.values()].reduce(
          (a, v) => a + v,
          0,
        );
        const alert =
          !alertedRef.current &&
          capRef.current &&
          b.alertAt &&
          spentRef.current + running >= b.alertAt * capRef.current;
        if (alert) alertedRef.current = true;
        syncSpend(alert ? { alert: true } : {});
        /* Held at the limit, the run ends when the last running scenario does. */
        if (stopRef.current && inflightRef.current.size === 0) settleAtLimit();
      }, t);

      /* A resumed task was the last thing holding the run open. */
      if (from > 0) {
        push(() => {
          holdRef.current = false;
          if (doneWaitingRef.current) {
            doneWaitingRef.current = false;
            setPhase("done");
          }
        }, t + 500);
      }

      return t;
    },
    // settleAtLimit only reads refs and setters; it's declared below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [patchTask, speed, plan],
  );
  driveRef.current = driveTask;

  const start = useCallback(() => {
    clearTimers();
    setPhase("running");

    // Each worker lane runs its tasks back to back; lanes run in parallel.
    const lanes = {};
    run.tasks.forEach((task) => {
      const lane = task.worker;
      const startAt = (lanes[lane] || 0) + Math.random() * 200;
      lanes[lane] = driveTask(task, startAt, {
        blockAt: plan?.taskId === task.id ? plan.step : null,
      });
    });

    const total = Math.max(...Object.values(lanes), 1000);
    timers.current.push(
      setTimeout(() => {
        /* Still waiting on an answer: finish when that scenario does. */
        if (holdRef.current) doneWaitingRef.current = true;
        else setPhase("done");
      }, total + 500),
    );
  }, [run.tasks, driveTask, plan]);

  /* Answer the open question; the held scenario picks up from the next turn. */
  const answer = useCallback(
    (text) => {
      if (!blocked) return;
      const entry = {
        taskId: blocked.taskId,
        question: blocked.question,
        answer: text,
        at: new Date().toISOString(),
      };
      setClarifications((c) => [...c, entry]);
      patchTask(blocked.taskId, { blocked: false, clarified: entry });
      setBlocked(null);
      const resume = resumeRef.current;
      resumeRef.current = null;
      resume?.();
    },
    [blocked, patchTask],
  );

  /* Once the limit is reached nothing waiting will start — said at once, not
     one scenario at a time as each one's turn comes up. */
  function holdQueued() {
    setTasks((ts) =>
      ts.map((t) => {
        if (t.status !== "queued") return t;
        skippedRef.current.add(t.id);
        return { ...t, status: "notStarted" };
      }),
    );
    setLastEventAt(Date.now());
  }
  /* Every scenario still waiting is marked as never started, and the run ends. */
  const settleAtLimit = () => {
    clearTimers();
    holdQueued();
    syncSpend({ reached: stopRef.current || "limit" });
    setLastEventAt(Date.now());
    setPhase("done");
  };

  /* The warning's choices. Continue keeps going to the limit; a lower limit
     cuts the rest; Stop lets the running scenarios finish and starts no more. */
  const dismissAlert = useCallback(
    () => setSpend((s) => ({ ...s, alert: false })),
    [],
  );
  const setCap = useCallback((n) => {
    capRef.current = n;
    const running = [...inflightRef.current.values()].reduce(
      (a, v) => a + v,
      0,
    );
    if (spentRef.current + running >= n) {
      stopRef.current = "limit";
      holdQueued();
    }
    syncSpend({ alert: false, reached: stopRef.current || null });
    if (stopRef.current && inflightRef.current.size === 0) settleAtLimit();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const stopAtLimit = useCallback(() => {
    stopRef.current = "stopped";
    holdQueued();
    syncSpend({ alert: false, reached: "stopped" });
    if (inflightRef.current.size === 0) settleAtLimit();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /*
    Stop, for the demo, means "the run is over": every scenario jumps to the
    verdict it was always going to reach, and the run finishes normally —
    recorded, published and handed to the Runs tab like any other.
  */
  const finishNow = useCallback(() => {
    clearTimers();
    clearHold();
    setTasks((ts) =>
      ts.map((t) => ({
        ...t,
        blocked: false,
        status: t.status === "notStarted" ? t.status : t.verdict,
        stepIndex:
          t.failStep != null
            ? Math.min(t.failStep, t.steps.length - 1)
            : t.steps.length - 1,
        evalIndex: t.evalResults.length,
      })),
    );
    setLastEventAt(Date.now());
    setPhase("done");
  }, []);

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
    /* Pulled (test invalid twice) and Outdated / Re-check scenarios are left
       out of every count — listed on their own. */
    const counted = tasks.filter((t) => !t.pulled && !staleIds?.[t.id]);
    const passed = counted.filter((t) => t.status === "passed").length;
    const failed = counted.filter((t) => t.status === "failed").length;
    /* Scenarios whose samples disagreed with each other. Counted apart from
       both — calling them failures blames the agent for the scenario. */
    const flaky = counted.filter((t) => t.status === "flaky").length;
    /* Scenarios nothing could be measured on — ours to fix, not the agent's. */
    const unmeasuredTasks = counted.filter((t) => t.status === "unmeasured");
    const unmeasured = unmeasuredTasks.length;
    /* …by outcome: inconclusive, test invalid, error. */
    const notCounted = { inconclusive: 0, "test-invalid": 0, error: 0 };
    unmeasuredTasks.forEach((t) => {
      const o = outcomeOfCause(causeOf(t));
      if (o in notCounted) notCounted[o] += 1;
    });
    const measured = done.filter(
      (t) => t.status !== "unmeasured" && !t.pulled && !staleIds?.[t.id],
    );
    const active = tasks.filter((t) =>
      ["running", "grading"].includes(t.status),
    ).length;
    const notStarted = tasks.filter((t) => t.status === "notStarted").length;
    return {
      total: tasks.length,
      done: done.length,
      passed,
      failed,
      flaky,
      unmeasured,
      notCounted,
      pulled: done.filter((t) => t.pulled).length,
      stale: done.filter((t) => staleIds?.[t.id]).length,
      retried: done.filter((t) => t.retried).length,
      measured: measured.length,
      repeats: run.repeats || 1,
      active,
      queued: tasks.length - done.length - active - notStarted,
      notStarted,
      /* The mean pass proportion, so a scenario that passed two of three
         samples counts as two thirds rather than as a clean pass or a loss. */
      /* Over what was measurable, never over what was attempted. */
      passRate: measured.length
        ? measured.reduce(
            (a, t) => a + (t.passShare ?? (t.status === "passed" ? 1 : 0)),
            0,
          ) / measured.length
        : 0,
      progress: tasks.length
        ? ((done.length + notStarted) / tasks.length) * 100
        : 0,
      /* Cost the agent's side didn't report is unverified, not $0. */
      cost: done
        .filter((t) => !t.costUnverified)
        .reduce((a, t) => a + t.cost, 0),
      costUnverified: done.filter(
        (t) => t.costUnverified && t.status !== "unmeasured",
      ).length,
      tokens: done.reduce((a, t) => a + t.tokens, 0),
    };
  }, [tasks, run.repeats, staleIds]);

  return {
    phase,
    setPhase,
    start,
    finishNow,
    tasks,
    focus,
    focusId,
    setFocusId,
    stats,
    elapsed,
    /* Seconds since anything last changed, and whether that is long enough to
       stop calling it slow. */
    sinceLastEvent: Math.max(0, Math.round((now - lastEventAt) / 1000)),
    /* Waiting on a person is not a stall. */
    stalled: phase === "running" && !blocked && now - lastEventAt > 15000,
    speed,
    setSpeed,
    stage: run.stage,
    /* The open clarifying question, how to answer it, and what was answered. */
    blocked,
    answer,
    clarifications,
    /* The spending limit: used, running, the limit, and why it stopped. */
    spend,
    dismissAlert,
    setCap,
    stopAtLimit,
    notStartedIds: tasks
      .filter((t) => t.status === "notStarted")
      .map((t) => t.id),
  };
}
