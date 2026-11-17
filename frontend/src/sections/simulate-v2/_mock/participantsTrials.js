/*
  Comparing the trials of one scenario (proposal 8, 16 Nov — not in Nikhil's
  plan; built as a proposal for review).

  · varyParticipantsForTrial — the mock seeds a call's participants from the
    scenario, so every repeat had the same route. Real repeats don't: a worker
    that timed out once answers the next time, or isn't asked at all. Trial 1
    stays exactly as it was; later trials vary from their own seed.
  · routesOf — one row per trial, one column per step, matched across trials
    by who asked whom (the n-th time), never by message content.
  · alignTrials — two trials' rows lined up for the side-by-side view.
*/
import { hashSeed } from "./runStream";
import { summaryOf } from "./participants";

/** A later trial's participants: the worker that timed out in trial 1 may
 *  answer this time, or not be asked. Trial 1 (index 0) is unchanged. */
export const varyParticipantsForTrial = (p, trial) => {
  const idx = trial?.trialIndex || 0;
  if (!p || !idx || !p.observed || !p.workers?.length) return p;
  const q = structuredClone(p);
  const h = hashSeed(`${trial.trialId || trial.id}:route`) >>> 0;
  const passed = trial.status === "passed";
  q.workers
    .filter((w) => w.state === "timeout")
    .forEach((w, i) => {
      const r = (h >>> (i * 3)) % 6;
      /* A passing repeat either got its answer or never needed the worker;
         a failing one gets its answer every other trial (it still fails on
         its sub-goal), so repeats differ the way real ones do. */
      const drop = passed && r % 2 === 0;
      const answer = passed ? !drop : (idx + i) % 2 === 1;
      if (drop) {
        q.turns.forEach((t) => {
          t.workers = (t.workers || []).filter((x) => x !== w);
        });
        q.workers = q.workers.filter((x) => x !== w);
        q.notRun = [...(q.notRun || []), w.name];
        return;
      }
      if (answer) {
        w.state = "returned";
        w.endedAt = (w.delegatedAt ?? 0) + 3 + (r % 5);
        w.calls = (w.calls || []).map((c) =>
          c.output == null && !c.blocked
            ? { ...c, output: { status: "done" } }
            : c,
        );
      }
    });
  const turns = Number(/over (\d+) turns/.exec(p.summary || "")?.[1]) || 1;
  q.summary = summaryOf({
    coordinator: q.coordinator.name,
    workers: q.workers,
    observed: true,
    turns,
  });
  return q;
};

/* ── keys ── */
const stepKey = (s) =>
  s.kind === "tool" ? `tool:${s.from}>${s.tool}` : `go:${s.from}>${s.to}`;
const rowKey = (r, s) =>
  r.type === "go" || r.type === "tool" ? stepKey(s) : `back:${s.to}>${s.from}`;

const stateOf = (s) => {
  if (!s) return "not asked";
  if (s.kind === "tool")
    return s.bad ? (s.refused ? "refused" : "failed") : "done";
  if (s.answer?.kind === "none")
    return s.answer.bad ? "no answer" : "no events";
  if (s.answer?.kind === "took") return "took over";
  if (s.bad || s.answer?.bad) return "failed";
  return "answered";
};
const took = (sec) =>
  sec == null
    ? ""
    : sec < 10
      ? `${sec.toFixed(1)} s`
      : sec < 60
        ? `${Math.round(sec)} s`
        : `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, "0")}`;

/**
 * trials: [{ idx, status, seq, placed }] (placed = placeFailures output).
 * → { columns: [{ key, label, title }], rows: [{ idx, status, cells, result }],
 *     split: { key, label, text } | null }
 */
export const routesOf = (trials, { showTools = false } = {}) => {
  const nameOf = (t, id) => t.seq.nameOf(id);
  /* Each trial's steps as keyed occurrences: "go:a>b#0", "go:a>b#1"… */
  const keyed = trials.map((t) => {
    const seen = {};
    /* A step standing for N turns answered alone counts N times, so a
       trial that asked twice is never read as "not asked". */
    return t.seq.steps
      .filter((s) => showTools || s.kind !== "tool")
      .flatMap((s) =>
        Array.from({ length: s.kind === "message" ? s.turns || 1 : 1 }, () => {
          const base = stepKey(s);
          const k = seen[base] || 0;
          seen[base] = k + 1;
          return { key: `${base}#${k}`, step: s };
        }),
      );
  });
  /* Column order: the first trial's, others' extras after their neighbour. */
  const order = [];
  const meta = {};
  keyed.forEach((list, ti) => {
    let prev = -1;
    list.forEach(({ key, step }) => {
      const at = order.indexOf(key);
      if (at >= 0) {
        prev = at;
        return;
      }
      order.splice(prev + 1, 0, key);
      prev += 1;
      const t = trials[ti];
      const again = Number(key.split("#")[1]) > 0;
      const to = step.kind === "tool" ? step.tool : nameOf(t, step.to);
      meta[key] = {
        label: `${nameOf(t, step.from)} → ${to}${again ? " (again)" : ""}`,
      };
    });
  });
  const rows = trials.map((t, ti) => {
    const byKey = Object.fromEntries(keyed[ti].map((x) => [x.key, x.step]));
    const cells = Object.fromEntries(
      order.map((key) => {
        const s = byKey[key];
        const state = stateOf(s);
        return [
          key,
          s
            ? {
                n: s.n,
                state,
                bad: ["no answer", "failed", "refused"].includes(state),
                text:
                  state === "answered" || state === "done"
                    ? took(s.dur) || state
                    : state,
                turns: s.turns > 1 ? s.turns : null,
              }
            : { n: null, state, bad: false, text: "not asked" },
        ];
      }),
    );
    const f = (t.placed || []).find(
      (x) => x.n != null && x.mark === "critical",
    );
    return {
      idx: t.idx,
      status: t.status,
      cells,
      result: f
        ? { text: f.text, at: f.row === "answer" ? `reply to step ${f.n}` : `step ${f.n}` }
        : null,
    };
  });
  /* The first column where the trials went differently. */
  const splitKey = order.find(
    (key) => new Set(rows.map((r) => r.cells[key].state)).size > 1,
  );
  const split = splitKey
    ? {
        key: splitKey,
        label: meta[splitKey].label,
        text: rows
          .map((r) => `trial ${r.idx + 1}: ${r.cells[splitKey].state}`)
          .join(" · "),
      }
    : null;
  return {
    columns: order.map((key) => ({ key, ...meta[key] })),
    rows,
    split,
  };
};

/**
 * Two trials' diagram rows lined up (longest common run of keys), each
 * { key, a, b, same } where a/b are { row, step } or null.
 */
export const alignTrials = (seqA, seqB) => {
  const units = (seq) => {
    const byN = Object.fromEntries(seq.steps.map((s) => [s.n, s]));
    return seq.rows.map((r) => ({
      key: rowKey(r, byN[r.n]),
      row: r,
      step: byN[r.n],
    }));
  };
  const A = units(seqA);
  const B = units(seqB);
  const L = Array.from({ length: A.length + 1 }, () =>
    new Array(B.length + 1).fill(0),
  );
  for (let i = A.length - 1; i >= 0; i -= 1)
    for (let j = B.length - 1; j >= 0; j -= 1)
      L[i][j] =
        A[i].key === B[j].key
          ? L[i + 1][j + 1] + 1
          : Math.max(L[i + 1][j], L[i][j + 1]);
  const out = [];
  let i = 0;
  let j = 0;
  const outcome = (u) =>
    u.row.type === "go" || u.row.type === "tool"
      ? `${u.row.type}:${!!u.step.bad}`
      : `${u.row.type}:${!!u.step.answer?.bad}`;
  while (i < A.length || j < B.length) {
    if (i < A.length && j < B.length && A[i].key === B[j].key) {
      out.push({
        key: A[i].key,
        a: A[i],
        b: B[j],
        same: outcome(A[i]) === outcome(B[j]),
      });
      i += 1;
      j += 1;
    } else if (j >= B.length || (i < A.length && L[i + 1][j] >= L[i][j + 1])) {
      out.push({ key: A[i].key, a: A[i], b: null, same: false });
      i += 1;
    } else {
      out.push({ key: B[j].key, a: null, b: B[j], same: false });
      j += 1;
    }
  }
  /* The same step or answer at a different moment (the order crossed, so
     the alignment couldn't pair them): each one points at its partner. */
  out.forEach((r) => {
    if (r.a && r.b) return;
    const mine = r.a || r.b;
    const partner = out.find(
      (x) =>
        x !== r &&
        x.key === r.key &&
        !x.partner &&
        (r.a ? x.b && !x.a : x.a && !x.b),
    );
    if (partner && !r.partner) {
      r.partner = partner.a || partner.b;
      partner.partner = mine;
    }
  });
  return out;
};
