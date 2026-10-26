import {
  W_LIFESTAGE,
  D_VALENCE,
  X_FIDELITY,
  I_STRUCTURE,
  O_TYPES,
  lifeStageOf,
  valenceOf,
  fidelityOf,
  structureOf,
  overlayOf,
} from "../../_mock/coverage";
import { deriveUseCaseLabel } from "../../run/traceTableHelpers";

/*
  The coverage axes, in plain words — eight ways scenarios can differ. Each
  axis reads a scenario to one level; the levels an axis shows are the ones
  the suite actually has, each with how many scenarios sit on it. Coverage
  asks "what kinds of situations did we forget to test?", so the gaps are
  the empty cells of the pairwise grid and the forced overlays, not a list
  of levels no one wrote.
*/

const labelFrom = (list, id) => list.find((o) => o.id === id)?.label || id;
const textOf = (s) =>
  [s.title, s.task, s.situation, s.summary].filter(Boolean).join(" ");
const attackOf = (s) => overlayOf(s);
const NO_ATTACK = "No attack";

export const COVERAGE_AXES = [
  {
    id: "task",
    label: "Task",
    hint: "What the caller is trying to get done",
    of: (s) => deriveUseCaseLabel(s) || "Other",
  },
  {
    id: "caller",
    label: "Who is calling",
    hint: "Who the caller is",
    of: (s) => labelFrom(W_LIFESTAGE, lifeStageOf(s)),
  },
  {
    id: "situation",
    label: "Their situation",
    hint: "The state they're in when they call",
    of: (s) => labelFrom(D_VALENCE, valenceOf(s)),
  },
  {
    id: "line",
    label: "Line and speech",
    hint: "How clearly they come through",
    of: (s, env) => labelFrom(X_FIDELITY, fidelityOf(s, env)),
  },
  {
    id: "flow",
    label: "How the call goes",
    hint: "How long and how winding the conversation is",
    of: (s) => labelFrom(I_STRUCTURE, structureOf(s)),
  },
  {
    id: "attack",
    label: "Attack",
    hint: "What the caller tries to get the agent to do wrong",
    of: (s) => {
      const o = attackOf(s);
      return o === "none" ? NO_ATTACK : labelFrom(O_TYPES, o);
    },
  },
  {
    id: "method",
    label: "Attack method",
    hint: "How the attack reaches the agent",
    of: (s) => {
      if (attackOf(s) === "none") return NO_ATTACK;
      return /document|tool result|email|upload|footer|pastes|quotes/i.test(
        textOf(s),
      )
        ? "Hidden in content"
        : "Spoken by the caller";
    },
  },
  {
    id: "intensity",
    label: "Attack intensity",
    hint: "How openly the caller pushes",
    of: (s) => {
      if (attackOf(s) === "none") return NO_ATTACK;
      return s.critical ||
        /repeat|insist|threat|demand|ten different|again/i.test(textOf(s))
        ? "Overt"
        : "Subtle";
    },
  },
];

export const getCoverageAxis = (id) =>
  COVERAGE_AXES.find((a) => a.id === id) || COVERAGE_AXES[0];

/* Levels an axis has in this suite, most-used first: [{ key, n }]. */
const levelsOf = (axis, scenarios, env) => {
  const counts = new Map();
  scenarios.forEach((s) => {
    const k = axis.of(s, env);
    counts.set(k, (counts.get(k) || 0) + 1);
  });
  return [...counts.entries()]
    .map(([key, n]) => ({ key, n }))
    .sort((a, b) => b.n - a.n || String(a.key).localeCompare(String(b.key)));
};

/** One row per axis: { id, label, hint, levels: [{ key, n }] }. */
export const axesCoverage = (scenarios, env) =>
  COVERAGE_AXES.map((axis) => ({
    id: axis.id,
    label: axis.label,
    hint: axis.hint,
    levels: levelsOf(axis, scenarios, env),
  }));

/**
 * Pairs: across every pair of axes, the share of level combinations the
 * suite has at least one scenario for — the levels being those it has.
 */
export const pairsShare = (scenarios, env) => {
  const ratios = [];
  for (let i = 0; i < COVERAGE_AXES.length; i += 1) {
    for (let j = i + 1; j < COVERAGE_AXES.length; j += 1) {
      const a = COVERAGE_AXES[i];
      const b = COVERAGE_AXES[j];
      const la = levelsOf(a, scenarios, env).length;
      const lb = levelsOf(b, scenarios, env).length;
      if (!la || !lb) continue;
      const filled = new Set(
        scenarios.map((s) => `${a.of(s, env)}|${b.of(s, env)}`),
      );
      ratios.push(filled.size / (la * lb));
    }
  }
  return ratios.length ? ratios.reduce((x, y) => x + y, 0) / ratios.length : 0;
};

/** The pairwise grid for two axes, over the levels each has. */
export const buildCoverageMatrix = (scenarios, env, rowId, colId) => {
  const rowAxis = getCoverageAxis(rowId);
  const colAxis = getCoverageAxis(colId);
  const byLabel = (a, b) => String(a).localeCompare(String(b));
  const rowKeys = levelsOf(rowAxis, scenarios, env)
    .map((l) => l.key)
    .sort(byLabel);
  const colKeys = levelsOf(colAxis, scenarios, env).map((l) => l.key);
  const counts = {};
  scenarios.forEach((s) => {
    const key = `${rowAxis.of(s, env)}|${colAxis.of(s, env)}`;
    counts[key] = (counts[key] || 0) + 1;
  });
  const max = Math.max(1, ...Object.values(counts));
  return {
    rowAxis: { ...rowAxis, labelOf: (k) => k },
    colAxis: { ...colAxis, labelOf: (k) => k },
    rowKeys,
    colKeys,
    max,
    at: (r, c) => counts[`${r}|${c}`] || 0,
  };
};
