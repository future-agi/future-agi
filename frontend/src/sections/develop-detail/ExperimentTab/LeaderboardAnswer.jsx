import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import { useNavigate } from "react-router";
import {
  Box,
  Button,
  Divider,
  Link,
  Menu,
  MenuItem,
  Popover,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import VerdictLine, {
  VerdictText,
  VerdictToneDot,
} from "src/sections/common/ux/VerdictLine";
import Term from "src/sections/common/ux/Term";
import { taskCompareHref } from "src/sections/common/Analysis/analysisLinks";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";
import {
  compareRuns,
  diffRange95,
  notRunText,
  rowLabelOf,
  rowRangesText,
} from "src/sections/common/Runs/runNumbers";
import { runKindOf, sameOutput } from "src/sections/common/Runs/runKind";
import { runSpokenName } from "src/sections/common/Runs/runLabel";
import { getRunStatus } from "src/sections/common/Runs/runStatus";
import { agentShortName } from "src/sections/experiment-detail/matrix/matrixModel";
import { verdictOfCompare } from "src/sections/experiment-detail/matrix/verdictScope";
import {
  VERDICT_WORD,
  matrixPairedSuccess,
  servedSuccessOf,
  withPairedCompare,
} from "src/sections/common/ux/verdictModel";
import { useMatrix } from "src/api/develop/r2";
import {
  answerLines,
  costPerTask,
  coverageOf,
  headlineRank,
  headlineVerdict,
  latestVsBaseline,
  madeAfter,
  shipLine,
  shownMs,
  timePerTaskMs,
} from "./leaderboardHeadline";
import {
  compareUrl,
  experimentRunsOf,
  experimentUrl,
  modelOf,
  partialProgress,
} from "./leaderboardModel";
import { experimentSources } from "./ExperimentNameCell";
import { identityOf } from "./leaderboardVersions";
import ComparePairPicker from "./ComparePairPicker";
import { waitNounOfAll } from "../Experiment/waitingModel";

/**
 * The default pair (IT-R1 G2 P2; IT-R2 G2 P6). The latest vs the previous
 * version of the same agent when both are in view AND put out the same
 * thing; else the best VISIBLE run that puts out what the baseline puts out
 * vs the baseline (a search or filter narrows it). A text-only voice run, an
 * image-output task or a spoken opening line is never paired by default
 * with a different output (it stays pickable under `Change runs`).
 * `agent` names the agent for the `Only one version …` sentence.
 * @param {object} numbers  useLeaderboardNumbers()
 * @param {object[]} visible  the runs on screen (filtered)
 * @param {object|null} baselineRun
 * @returns {{candId, baseId, kind: "pair"|"single", agent: string|null}|null}
 */
export function defaultPair(numbers, visible, baselineRun, selectedIds) {
  const shown = new Set((visible ?? []).map((r) => r.run_id));
  // IT-R3 G2 P1 / P2: a run with nothing scored never headlines.
  const scored = (r) =>
    (numbers?.byRun?.[r.run_id]?.rate?.n ?? 0) > 0 ||
    (r?.success_rate?.total ?? 0) > 0;
  // IT-R3 G2 P5: two or more ticked runs → the headline follows them.
  const picked = [
    ...(visible ?? []),
    ...(baselineRun && !shown.has(baselineRun.run_id) ? [baselineRun] : []),
  ].filter((r) => selectedIds?.has?.(r.run_id));
  if (picked.length >= 2) {
    const rateOf = (r) => numbers?.byRun?.[r.run_id]?.rate?.rate ?? -1;
    const base =
      picked.find((r) => r.run_id === baselineRun?.run_id) ??
      [...picked].sort((a, b) => rateOf(a) - rateOf(b))[0];
    const cand = picked
      .filter((r) => r.run_id !== base.run_id && scored(r))
      .sort((a, b) => rateOf(b) - rateOf(a))[0];
    if (cand)
      return {
        candId: cand.run_id,
        baseId: base.run_id,
        kind: "selection",
        agent: identityOf(base)?.name ?? null,
      };
  }
  const pair = numbers?.pair;
  if (
    pair &&
    shown.has(pair.latest.run_id) &&
    shown.has(pair.previous.run_id) &&
    sameOutput(pair.latest, pair.previous) &&
    scored(pair.latest) &&
    scored(pair.previous)
  )
    return {
      candId: pair.latest.run_id,
      baseId: pair.previous.run_id,
      kind: "pair",
      agent: identityOf(pair.latest)?.name ?? null,
    };
  const base = baselineRun?.run_id ?? null;
  const rate = (r) => numbers?.byRun?.[r.run_id]?.rate?.rate ?? -1;
  // IT-R3 G2 P1: like for like first (same dataset version as the
  // baseline, every task), then the best success, then the cheaper.
  const rank = (r) => headlineRank(r, baselineRun, scored(r));
  const cand = (visible ?? [])
    .filter(
      (r) =>
        r.run_id !== base &&
        r.listed !== false &&
        rank(r) != null &&
        (!baselineRun || sameOutput(r, baselineRun)),
    )
    .sort(
      (a, b) =>
        rank(a) - rank(b) ||
        rate(b) - rate(a) ||
        (costPerTask(a) ?? Infinity) - (costPerTask(b) ?? Infinity),
    )[0];
  if (!base || !cand) return null;
  return {
    candId: cand.run_id,
    baseId: base,
    kind: "single",
    // The baseline's agent (the `old` one), else the candidate's.
    agent:
      identityOf(baselineRun)?.name ??
      identityOf(cand)?.name ??
      agentShortName(cand) ??
      null,
  };
}

/** The name a person says (common/Runs/runLabel.runSpokenName). */
export const answerName = (run) => runSpokenName(run) || null;

/**
 * IT-R5 G2 P5 (rule 37): `#2D the coding harness Code`; the bare label when there is
 * no other name.
 */
const whoOf = (name, label) =>
  name && name !== label ? `${label} ${name}` : label;

/** `Change runs ▾`: the two pickers, the one-version sentence, tick any. */
function ChangeRunsButton({
  lead,
  pool,
  candId,
  baseId,
  roles,
  onPick,
  onCompareAny,
  compareAnyOn,
  sideHref,
  compareHref,
}) {
  const anchorRef = useRef(null);
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        ref={anchorRef}
        size="small"
        color="inherit"
        onClick={() => setOpen(true)}
        endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
        data-testid="leaderboard-change-runs"
        sx={{ typography: "s2", height: 28, px: 1, whiteSpace: "nowrap" }}
      >
        Change runs
      </Button>
      <Popover
        open={open}
        anchorEl={anchorRef.current}
        onClose={() => setOpen(false)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{ paper: { sx: { mt: 0.5, p: 1.5, maxWidth: 560 } } }}
      >
        <Stack spacing={1} data-testid="leaderboard-pair">
          <Typography
            typography="s2"
            color="text.secondary"
            data-testid="leaderboard-headline"
          >
            {lead}
          </Typography>
          <Stack direction="row" alignItems="center" gap={0.75} flexWrap="wrap">
            <ComparePairPicker
              runs={pool}
              value={candId}
              otherId={baseId}
              roles={roles}
              ariaLabel="Run to compare"
              onChange={(id) => onPick({ cand: id })}
            />
            <Typography typography="s2" color="text.secondary">
              vs
            </Typography>
            <ComparePairPicker
              runs={pool}
              value={baseId}
              otherId={candId}
              roles={roles}
              ariaLabel="Compare with"
              onChange={(id) => onPick({ base: id })}
            />
          </Stack>
          <Divider />
          <Stack direction="row" gap={2} flexWrap="wrap" alignItems="center">
            {/* IT-R3 G2 STRETCH: the popover carries its own Compare. */}
            {compareHref ? (
              <Button
                component={RouterLink}
                to={compareHref}
                size="small"
                variant="outlined"
                color="primary"
                onClick={() => setOpen(false)}
                data-testid="leaderboard-change-runs-compare"
                sx={{ height: 28, whiteSpace: "nowrap" }}
              >
                Compare
              </Button>
            ) : null}
            {sideHref ? (
              <Link
                component={RouterLink}
                to={sideHref}
                typography="s2"
                underline="hover"
                data-testid="leaderboard-see-why"
              >
                Side by side, task by task
              </Link>
            ) : null}
            {onCompareAny ? (
              <Link
                component="button"
                type="button"
                typography="s2"
                underline="hover"
                onClick={() => {
                  setOpen(false);
                  onCompareAny();
                }}
                aria-pressed={compareAnyOn}
                data-testid="compare-any-runs"
              >
                {compareAnyOn ? "Done picking" : "Tick 2–6 runs in the table…"}
              </Link>
            ) : null}
          </Stack>
        </Stack>
      </Popover>
    </>
  );
}

ChangeRunsButton.propTypes = {
  lead: PropTypes.string,
  pool: PropTypes.array.isRequired,
  candId: PropTypes.string,
  baseId: PropTypes.string,
  roles: PropTypes.object,
  onPick: PropTypes.func.isRequired,
  onCompareAny: PropTypes.func,
  compareAnyOn: PropTypes.bool,
  sideHref: PropTypes.string,
  compareHref: PropTypes.string,
};

/**
 * IT-R3 G2 P2: experiments whose runs have nothing scored yet, said once
 * per experiment — `#4 waits for your terminal (3 runs).` — never a
 * verdict. `#4A` when the experiment has one such run. The noun follows
 * the runs' mode (`the CI job`, `uploads`, `the sandbox`).
 */
export function waitingNote(runs) {
  const groups = new Map();
  (runs ?? []).forEach((r) => {
    if (!r || r.listed === false) return;
    const st = getRunStatus(r);
    if (!st.waiting && !st.active) return;
    if ((r.success_rate?.total ?? 0) > 0 || (r.progress?.done ?? 0) > 0) return;
    const key = r.experiment_id ?? r.run_id;
    const g = groups.get(key) ?? { runs: [], waiting: 0 };
    g.runs.push(r);
    if (st.waiting) g.waiting += 1;
    groups.set(key, g);
  });
  if (!groups.size) return null;
  return [...groups.values()]
    .map((g) => {
      const who =
        g.runs.length === 1
          ? g.runs[0].label
          : `#${g.runs[0].experiment_ordinal ?? String(g.runs[0].label ?? "").replace(/^#(\d+).*/, "$1")}`;
      const what =
        g.waiting === g.runs.length
          ? `waits for ${waitNounOfAll(g.runs)}`
          : "is starting";
      return `${who} ${what}${g.runs.length > 1 ? ` (${g.runs.length} runs)` : ""}.`;
    })
    .join(" ");
}

/**
 * The leaderboard's answer (R4 lane Rb MUST 7; IT-R2 G2 P2/P3/P5/P6;
 * IT-R3 G2 P1/P5): one sentence, the ship line, one primary action —
 *
 *   ✓ the coding harness Code (#2D) passes more tasks than Support Agent v3 (#2B,
 *     baseline): 91.0% vs 65.6% on all 30 tasks · 10 better · 1 worse
 *     Don't ship #2D the coding harness Code yet: it fails T25, which #2B passes.
 *     Open T25 · [Compare these two] · +2 more
 *
 * `+2 more` opens the cost / time sentence, `Change runs` and
 * `Confidence`. The pair is like for like (same dataset version as the
 * baseline, every task) whenever such a run exists; a partial run says so
 * in its first sentence. Every number is runNumbers.compareRuns over the
 * leaderboard's one matrix (useLeaderboardNumbers), so the headline, the
 * compare header, the Matrix and the Summary print the same counts.
 */
/**
 * IT-R5 G2 P7: the frameworks and harnesses an experiment ran, once each
 * (`LangGraph`, `CrewAI`, `the coding harness Code`…); prompts and agent graphs are not
 * counted.
 */
function frameworksOf(runs) {
  return experimentSources(
    runs.filter((r) => ["code", "harness"].includes(runKindOf(r))),
  );
}

/**
 * `Also here: #3 compares 6 frameworks and harnesses [Open #3]` when the
 * latest experiment holds fewer frameworks than another on the dataset.
 */
export function alsoHereOf(pool) {
  const byExp = new Map();
  (pool ?? []).forEach((r) => {
    if (!r?.experiment_id || r.listed === false) return;
    if (!byExp.has(r.experiment_id)) byExp.set(r.experiment_id, []);
    byExp.get(r.experiment_id).push(r);
  });
  if (byExp.size < 2) return null;
  const at = (r) => Date.parse(r?.created_at ?? "") || 0;
  const newest = Math.max(...[...byExp.values()].flat().map(at));
  const latestId = [...byExp.entries()].find(([, rs]) =>
    rs.some((r) => at(r) === newest),
  )?.[0];
  const latestCount = frameworksOf(byExp.get(latestId) ?? []).length;
  let best = null;
  byExp.forEach((rs, id) => {
    if (id === latestId) return;
    const fw = frameworksOf(rs);
    if (fw.length < 3 || fw.length <= latestCount) return;
    if (!best || fw.length > best.frameworks.length)
      best = { id, runs: rs, frameworks: fw };
  });
  if (!best) return null;
  const kinds = new Set(best.runs.map(runKindOf));
  const what =
    kinds.has("code") && kinds.has("harness")
      ? "frameworks and harnesses"
      : kinds.has("harness")
        ? "harnesses"
        : "frameworks";
  const ordinal = best.runs[0]?.experiment_ordinal;
  return {
    experimentId: best.id,
    label: `#${ordinal}`,
    text: `Also here: #${ordinal} compares ${best.frameworks.length} ${what}`,
    tip: best.frameworks.map((x) => x.name).join(", "),
  };
}

export default function LeaderboardAnswer({
  runs,
  allRuns,
  numbers,
  baselineRun,
  datasetId,
  datasetKind,
  fromUrl,
  onCompareAny,
  compareAnyOn = false,
  selectedIds = null,
  pinnedRunId = null,
  rowLabels = null,
}) {
  const navigate = useNavigate();
  const [more, setMore] = useState(false);
  const pool = useMemo(() => {
    const list = (allRuns ?? runs).filter((r) => r && r.listed !== false);
    if (baselineRun && !list.some((r) => r.run_id === baselineRun.run_id))
      return [baselineRun, ...list];
    return list;
  }, [allRuns, runs, baselineRun]);
  const byId = useMemo(
    () => Object.fromEntries(pool.map((r) => [r.run_id, r])),
    [pool],
  );
  const def = useMemo(
    () => defaultPair(numbers, runs, baselineRun, selectedIds),
    [numbers, runs, baselineRun, selectedIds],
  );
  const [picked, setPicked] = useState({});
  const candId =
    picked.cand && byId[picked.cand] ? picked.cand : def?.candId ?? null;
  const baseId =
    picked.base && byId[picked.base] ? picked.base : def?.baseId ?? null;
  const cand = candId ? byId[candId] : null;
  const base = baseId ? byId[baseId] : null;

  // P5: `baseline` only on the pinned run; the older sibling of the
  // newest version is the `previous version`.
  const roles = useMemo(() => {
    const r = {};
    if (baselineRun?.run_id) r[baselineRun.run_id] = "baseline";
    if (numbers?.pair) {
      if (!r[numbers.pair.latest.run_id])
        r[numbers.pair.latest.run_id] = "newest";
      if (!r[numbers.pair.previous.run_id])
        r[numbers.pair.previous.run_id] = "previous version";
    }
    return r;
  }, [numbers?.pair, baselineRun?.run_id]);

  // integration C3 (review 2, M1-residual): the pair's paired verdict, served by
  // the ONE estimator the Summary decides with — the run list's
  // `vs_baseline` when `base` is the list baseline, else the pair's own R3
  // read with its baseline (two ticked runs, a previous version): the same
  // read the Compare page makes for this pair.
  const listPaired =
    cand && base ? servedSuccessOf(cand.success_rate?.vs_baseline, base.run_id) : null; // prettier-ignore
  const pairQ = useMatrix(
    {
      datasetId,
      runIds: cand && base ? [base.run_id, cand.run_id] : [],
      baselineId: base?.run_id,
    },
    {
      enabled: Boolean(cand && base && cand !== base && !listPaired),
      refetchOnWindowFocus: false,
    },
  );
  const pairedNow =
    listPaired ?? matrixPairedSuccess(pairQ.data, base?.run_id, cand?.run_id);
  const pairedKey = pairedNow
    ? `${pairedNow.deltaPts}|${pairedNow.ci95Pts}|${pairedNow.nPaired}|${pairedNow.significant}`
    : "";
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const paired = useMemo(() => pairedNow, [pairedKey]);

  const compare = useMemo(() => {
    if (!numbers?.matrix || !cand || !base || cand === base) return null;
    const labels = Object.fromEntries(pool.map((r) => [r.run_id, r.label]));
    const c = compareRuns({
      matrix: numbers.matrix,
      baselineId: base.run_id,
      candidateId: cand.run_id,
      labels,
      noun: numbers.noun ?? "task",
    });
    // The compare carries the served verdict, so verdictOfCompare and
    // answerLines decide with the Summary's interval.
    return c.rows.length ? withPairedCompare(c, paired) : null;
  }, [numbers?.matrix, numbers?.noun, cand, base, pool, paired]);

  const noun = numbers?.noun ?? "task";
  const pairOf = (fn) => {
    const a = fn(cand);
    const b = fn(base);
    return Number.isFinite(a) && Number.isFinite(b)
      ? { cand: a, base: b }
      : undefined;
  };
  // IT-R3 G2 P1: the rows the candidate skipped, and why (another dataset
  // version, or fewer tasks) — the first sentence says so.
  const partialRun = useMemo(() => {
    if (!compare || compare.partial || !cand || !base) return null;
    const skipped = compare.rows
      .filter((r) => r.missing === "candidate" || r.missing === "both")
      .map((r) => r.n);
    if (!skipped.length) return null;
    const cov = coverageOf(cand, base);
    const ran = cov.sameVersion
      ? `${compare.rows.length - skipped.length} of ${plainTasks(compare.rows.length, noun)}`
      : `dataset v${cov.version}`;
    const text = rowRangesText(skipped, datasetKind);
    return {
      ran,
      missing:
        text.split(", ").length <= 3 ? text : `${skipped.length} ${noun}s`,
      missingCount: skipped.length,
      total: compare.rows.length,
    };
  }, [compare, cand, base, datasetKind, noun]);

  // Rule 17: two runs never read the same; same name → add the model.
  const names = useMemo(() => {
    if (!cand || !base) return { cand: null, base: null };
    let c = answerName(cand);
    let b = answerName(base);
    if (c && c === b) {
      const cm = modelOf(cand);
      const bm = modelOf(base);
      // IT-R5 G2 P5: the short form — `#1B gemini-2.5-flash` vs `#1A
      // gpt-4o`, never `Damage and contract extraction V1 · …`.
      if (cm && bm && cm !== bm) {
        c = cm;
        b = bm;
      }
    }
    return { cand: c, base: b };
  }, [cand, base]);

  const answer = useMemo(() => {
    if (!compare) return null;
    const cost = pairOf(costPerTask);
    // The displayed values (`74 s` vs `9.8 s` → `64 s slower`).
    const time = pairOf((r) => shownMs(timePerTaskMs(r)));
    const verdict = verdictOfCompare(compare, {
      candidate: { label: cand.label, name: agentShortName(cand) },
      baseline: { label: base.label, name: agentShortName(base) },
      noun,
      tries: Math.max(1, Number(cand.trials) || 1),
      costPerTask: cost,
      timePerTask: time,
    });
    return answerLines({
      compare,
      verdict,
      cand: {
        label: cand.label,
        name: names.cand,
        // IT-R4 G2 P5: made after the baseline → `New`; else the newer run
        // of the pair carries `newest`.
        role:
          roles[base.run_id] === "baseline" && madeAfter(cand, base)
            ? "new"
            : roles[cand.run_id] === "newest"
              ? "newest"
              : null,
      },
      base: {
        label: base.label,
        name: names.base,
        role: roles[base.run_id] === "newest" ? null : roles[base.run_id],
      },
      noun,
      // A run still going has not run the rest YET: say that, not `did
      // not run 228 of 238`.
      notRun: compare.partial
        ? null
        : notRunText(compare, {
            candidate: names.cand ?? cand.label,
            baseline: names.base ?? base.label,
            datasetKind,
            noun,
          }),
      costPerTask: cost,
      timePerTask: time,
      partialRun,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [compare, cand, base, noun, roles, datasetKind, names, partialRun]);

  // IT-R3 G2 P1 / S6: the ship line is part of the verdict.
  const ship = useMemo(
    () =>
      compare
        ? shipLine(compare, {
            // IT-R4 finalize (rule 27): the same `Name (#2G)` form as the
            // too-close-to-call line above and the Matrix / Summary ship line.
            cand: whoOf(names.cand, cand.label),
            candLabel: cand.label,
            base: whoOf(names.base, base.label),
            rowLabel: (n) => rowLabelOf(n, datasetKind, rowLabels),
            // IT-R4 G2 P8: a voice call's caller (`all with Mike`).
            rowGroup: rowLabels
              ? (n) => String(rowLabels[n] ?? "").split(" · ")[0] || null
              : undefined,
            noun,
            partial: Boolean(partialRun),
            total: partialRun?.total ?? null,
          })
        : null,
    [compare, cand, base, names, datasetKind, noun, partialRun, rowLabels],
  );
  const waiting = useMemo(() => waitingNote(allRuns ?? runs), [allRuns, runs]);

  // R3 sentence when the matrix is not there yet (or no pair at all).
  const legacy = useMemo(
    () => (answer ? null : headlineVerdict(runs, baselineRun)),
    [answer, runs, baselineRun],
  );
  // IT-R5 G2 P1: `Reading the runs…` never stays. While the matrix loads
  // the line waits at most READING_MS; then (or at once on a failed read)
  // the runs' own numbers answer (legacy.verdict), then the waiting note.
  const reading = Boolean(
    !answer && def && numbers && !numbers.matrix && !numbers.matrixError,
  );
  const [readingTimedOut, setReadingTimedOut] = useState(false);
  useEffect(() => {
    if (!reading) {
      setReadingTimedOut(false);
      return undefined;
    }
    const t = setTimeout(() => setReadingTimedOut(true), READING_MS);
    return () => clearTimeout(t);
  }, [reading]);

  const ids = cand && base ? [base.run_id, cand.run_id] : null;
  // S6: `Compare these two` carries the pair (runs, cand, baseline).
  const compareHref = ids
    ? compareUrl(datasetId, {
        runIds: ids,
        baselineId: base.run_id,
        candId: cand.run_id,
        runs: [base, cand],
        from: fromUrl,
        tab: "matrix",
      })
    : null;
  const firstWorse = compare?.worseIds?.[0] ?? null;
  const sideHref = ids
    ? taskCompareHref({
        datasetId,
        runIds: ids,
        baselineId: base.run_id,
        rowId: firstWorse ?? undefined,
        from: fromUrl,
      })
    : null;

  // IT-R4 G2 P1: `Latest vs baseline` — the pinned baseline and the newest
  // finished run, on compare Data (S2: runs, baseline, cand).
  const latest = useMemo(() => {
    const pinned = pinnedRunId ? byId[pinnedRunId] : null;
    const lvb = latestVsBaseline(pool, pinned);
    if (!lvb) return null;
    return {
      ...lvb,
      href: compareUrl(datasetId, {
        runIds: lvb.runIds,
        baselineId: lvb.baselineId,
        candId: lvb.candId,
        runs: [lvb.base, lvb.cand],
        from: fromUrl,
        tab: "data",
      }),
      // IT-R5 G2 P3: `Latest finished run. #7B is still running (21%).`
      tip: lvb.skipped
        ? `Latest finished run. ${lvb.skipped.label} is still running${progressPct(lvb.skipped)}.`
        : `The newest run, ${lvb.cand.label}, next to baseline ${lvb.base.label}, task by task`,
      soFar: lvb.skipped
        ? {
            label: lvb.skipped.label,
            href: compareUrl(datasetId, {
              runIds: [lvb.baselineId, lvb.skipped.run_id],
              baselineId: lvb.baselineId,
              candId: lvb.skipped.run_id,
              runs: [lvb.base, lvb.skipped],
              from: fromUrl,
              tab: "data",
            }),
          }
        : null,
    };
  }, [pool, byId, pinnedRunId, datasetId, fromUrl]);

  // IT-R4 G2 P5 / IT-R5 G2 P2: the pinned baseline vs its own previous
  // run, whatever the verdict — `Baseline #2B vs its previous run #1B: too
  // close to call (95% range −8 to +8 pts) [Compare]`; a real drop is red
  // and names the tasks that got worse.
  const baselineTrend = useMemo(() => {
    const p = pinnedRunId ? numbers?.prevOf?.[pinnedRunId] : null;
    const pinned = pinnedRunId ? byId[pinnedRunId] : null;
    if (!p || !pinned) return null;
    const range = diffRange95(p.cand, p.base, p.n);
    if (!range) return null;
    const word = p.regressed
      ? "worse"
      : range.real && (p.deltaPts ?? 0) > 0
        ? "better"
        : VERDICT_WORD.tie.toLowerCase();
    const kind = p.kind === "run" ? "run" : "version";
    return {
      regressed: Boolean(p.regressed),
      lead: `Baseline ${pinned.label} vs its previous ${kind} ${p.previous.label}: ${word} on ${plainTasks(p.n, noun)} (`,
      range: String(range.text).replace(/^95% range\s*/, ""),
      worseText: `${plainTasks(p.worse, noun)} got worse`,
      href: compareUrl(datasetId, {
        runIds: [p.previous.run_id, pinned.run_id],
        baselineId: p.previous.run_id,
        candId: pinned.run_id,
        runs: [p.previous, pinned],
        from: fromUrl,
        tab: "data",
      }),
    };
  }, [numbers?.prevOf, pinnedRunId, byId, noun, datasetId, fromUrl]);

  const alsoHere = useMemo(() => {
    const a = alsoHereOf(pool);
    if (!a) return null;
    return {
      ...a,
      href: experimentUrl(datasetId, a.experimentId, {
        runs: experimentRunsOf(pool, a.experimentId),
      }),
    };
  }, [pool, datasetId]);

  // IT-R5 G2 P3: New and Old named under the headline — `New: #2D the coding harness
  // Code · Old: #2B Support Agent v3 (baseline · current)`.
  const roleLine = useMemo(() => {
    if (!cand || !base) return null;
    const flip = madeAfter(base, cand);
    const nameOf = (r) => (r === cand ? names.cand : names.base);
    const tagOf = (r) =>
      r.run_id === pinnedRunId
        ? "baseline · current"
        : roles[r.run_id] === "baseline"
          ? "baseline"
          : roles[r.run_id] === "previous version"
            ? "previous version"
            : null;
    const text = (r) => {
      const t = tagOf(r);
      return `${whoOf(nameOf(r), r.label)}${t ? ` (${t})` : ""}`;
    };
    // A candidate older than its baseline (a search, a picked pair) is not
    // `Old` next to a `New` baseline: the run being judged leads, named
    // for what it is.
    if (flip)
      return {
        newWord: "Checked:",
        oldWord: "Against:",
        newText: `${text(cand)} (an older run)`,
        oldText: text(base),
      };
    return {
      newWord: "New:",
      oldWord: "Old:",
      newText: text(cand),
      oldText: text(base),
    };
  }, [cand, base, names, roles, pinnedRunId]);

  useRegisterCommands(
    `leaderboard-answer:${datasetId}`,
    compareHref
      ? [
          {
            id: "leaderboard.latestVsPrevious",
            label:
              def?.kind === "pair"
                ? "Compare latest vs previous"
                : "Compare the best run with the baseline",
            keywords: [
              "latest",
              "previous",
              "version",
              "compare",
              "regression",
            ],
            icon: "mdi:compare-horizontal",
            run: () => navigate(compareHref),
          },
        ]
      : [],
    [compareHref, def?.kind],
  );

  // IT-R3 G2 P5: the answer's two lines are reserved while the matrix
  // loads (no late 58 px shift under the toolbar).
  if (reading && !readingTimedOut)
    return (
      <Stack
        sx={{ px: 1.5, pb: 0.25, minHeight: ANSWER_MIN_HEIGHT }}
        data-testid="leaderboard-answer"
        aria-busy="true"
      >
        <Typography typography="s2" color="text.disabled" sx={{ pl: 3.25 }}>
          Reading the runs…
        </Typography>
      </Stack>
    );
  if (!answer && !legacy?.verdict)
    return waiting ? (
      <Stack sx={{ px: 1.5, pb: 0.75 }} data-testid="leaderboard-answer">
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid="leaderboard-waiting"
        >
          {waiting}
        </Typography>
      </Stack>
    ) : null;
  if (!answer)
    return (
      <Stack
        sx={{ px: 1.5, pb: 0.75, minHeight: ANSWER_MIN_HEIGHT }}
        data-testid="leaderboard-answer"
      >
        <VerdictLine verdict={legacy.verdict} />
      </Stack>
    );

  const lead =
    def?.kind === "single"
      ? `Only one version of ${def.agent ?? "the agent"} ran here. Pick any two runs:`
      : "Pick any two runs:";
  const sentence = answer.parts.map((p) => p.text).join("");
  const moreCount = (answer.tradeoff ? 1 : 0) + (answer.confidence ? 1 : 0);
  const openRowHref =
    ship?.rowId && ids
      ? taskCompareHref({
          datasetId,
          runIds: ids,
          baselineId: base.run_id,
          rowId: ship.rowId,
          from: fromUrl,
        })
      : null;

  // IT-R5 G2 P5: one sentence per pair. A failed gate leads (`Don't ship
  // …`), the delta sentence follows; else the delta leads, the ship line
  // follows.
  const gateFails = ship?.tone === "worse";
  const shipText = ship ? (
    <>
      {ship.text}
      {openRowHref ? (
        <>
          {" "}
          <Link
            component={RouterLink}
            to={openRowHref}
            underline="always"
            data-testid="leaderboard-ship-row"
            sx={{ whiteSpace: "nowrap" }}
          >
            {ship.rowText}
          </Link>
        </>
      ) : null}
    </>
  ) : null;
  const deltaVerdict = { tone: answer.tone, parts: answer.parts, sentence };

  return (
    <Stack
      spacing={0.25}
      sx={{ px: 1.5, pb: 0.25, minWidth: 0, minHeight: ANSWER_MIN_HEIGHT }}
      data-testid="leaderboard-answer"
    >
      {gateFails ? (
        <Stack
          direction="row"
          alignItems="flex-start"
          gap={1}
          data-testid="verdict-line"
          data-tone="worse"
          sx={{ minWidth: 0 }}
        >
          <VerdictToneDot tone="worse" />
          <Typography
            component="p"
            typography="s1"
            fontWeight="fontWeightMedium"
            color="error.main"
            data-testid="leaderboard-ship"
            sx={{ m: 0, minWidth: 0 }}
          >
            {shipText}
          </Typography>
        </Stack>
      ) : (
        <VerdictLine verdict={deltaVerdict} />
      )}
      {/* Line 2: the other half of the same pair's sentence. */}
      {gateFails ? (
        <Box sx={{ pl: 3.25, minWidth: 0 }} data-testid="leaderboard-delta">
          <VerdictText verdict={deltaVerdict} secondary />
        </Box>
      ) : ship ? (
        <Typography
          typography="s2"
          fontWeight="fontWeightMedium"
          color="text.primary"
          data-testid="leaderboard-ship"
          sx={{ pl: 3.25, minWidth: 0 }}
        >
          {shipText}
        </Typography>
      ) : null}
      {/* Line 3: New and Old named (P3), the one primary, `+N more`. */}
      <Stack
        direction="row"
        alignItems="center"
        columnGap={1.25}
        rowGap={0.5}
        flexWrap="wrap"
        sx={{ pl: 3.25, minWidth: 0 }}
      >
        {roleLine ? (
          <Typography
            typography="s2"
            color="text.secondary"
            data-testid="leaderboard-roles"
            sx={{ minWidth: 0 }}
          >
            <Box component="span" sx={{ color: "text.primary" }}>
              {roleLine.newWord}
            </Box>{" "}
            {roleLine.newText}
            {" · "}
            <Box component="span" sx={{ color: "text.primary" }}>
              {roleLine.oldWord}
            </Box>{" "}
            {roleLine.oldText}
          </Typography>
        ) : null}
        <Stack
          direction="row"
          alignItems="center"
          gap={1}
          sx={{ flexShrink: 0 }}
        >
          {compareHref ? (
            <CustomTooltip
              show
              title="Every task and every try of these two runs, side by side in the Matrix"
              size="small"
              placement="top"
            >
              <Button
                component={RouterLink}
                to={compareHref}
                size="small"
                variant="outlined"
                color="primary"
                data-testid="compare-latest-previous"
                sx={{ whiteSpace: "nowrap", height: 28 }}
              >
                Compare these two
              </Button>
            </CustomTooltip>
          ) : null}
          {latest ? <LatestChip latest={latest} /> : null}
          <Button
            size="small"
            color="inherit"
            onClick={() => setMore((m) => !m)}
            aria-expanded={more}
            data-testid="leaderboard-more"
            sx={{ typography: "s2", height: 28, px: 1, whiteSpace: "nowrap" }}
          >
            {more ? "Less" : moreCount ? `+${moreCount} more` : "More"}
          </Button>
          {waiting ? (
            <Typography
              typography="s2"
              color="text.secondary"
              data-testid="leaderboard-waiting"
              sx={{ whiteSpace: "nowrap" }}
            >
              {waiting}
            </Typography>
          ) : null}
        </Stack>
      </Stack>
      {baselineTrend ? (
        <Typography
          typography="s2"
          color={baselineTrend.regressed ? "error.main" : "text.secondary"}
          data-testid={
            baselineTrend.regressed
              ? "leaderboard-regression"
              : "leaderboard-baseline-trend"
          }
          sx={{ pl: 3.25 }}
        >
          {baselineTrend.lead}
          <Term k="withinNoise">95% range</Term>
          {` ${baselineTrend.range})`}
          {baselineTrend.regressed ? ` · ${baselineTrend.worseText}` : ""}{" "}
          <Link
            component={RouterLink}
            to={baselineTrend.href}
            underline="always"
            data-testid="leaderboard-baseline-trend-compare"
          >
            Compare
          </Link>
        </Typography>
      ) : null}
      {alsoHere ? (
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid="leaderboard-also-here"
          sx={{ pl: 3.25 }}
        >
          <CustomTooltip show title={alsoHere.tip} size="small" arrow>
            <span>{alsoHere.text}</span>
          </CustomTooltip>{" "}
          <Link component={RouterLink} to={alsoHere.href} underline="hover">
            {`Open ${alsoHere.label}`}
          </Link>
        </Typography>
      ) : null}
      {more ? (
        <Stack
          direction="row"
          alignItems="center"
          columnGap={1.5}
          rowGap={0.5}
          flexWrap="wrap"
          sx={{ pl: 3.25, minWidth: 0 }}
          data-testid="leaderboard-more-lines"
        >
          {answer.tradeoff ? (
            <Typography
              typography="s2"
              color="text.secondary"
              data-testid="leaderboard-tradeoff"
              sx={{ minWidth: 0 }}
            >
              {answer.tradeoff}
            </Typography>
          ) : null}
          <ChangeRunsButton
            lead={lead}
            pool={pool}
            candId={candId}
            baseId={baseId}
            roles={roles}
            onPick={(p) => setPicked((cur) => ({ ...cur, ...p }))}
            onCompareAny={onCompareAny}
            compareAnyOn={compareAnyOn}
            sideHref={sideHref}
            compareHref={compareHref}
          />
          {answer.confidence ? (
            <Typography
              typography="s2"
              color="text.secondary"
              data-testid="leaderboard-confidence"
              sx={{ whiteSpace: "nowrap" }}
            >
              <Term k="confidence" extra={answer.confidence.extra}>
                Confidence
              </Term>
              {`: ${answer.confidence.level}`}
            </Typography>
          ) : null}
        </Stack>
      ) : null}
    </Stack>
  );
}

/**
 * IT-R5 G2 P3: `Latest #5A vs #1A ▾` — names its pair; when a newer run is
 * still going the tooltip says so and the menu offers `Compare #7B so far`.
 */
function LatestChip({ latest }) {
  const anchorRef = useRef(null);
  const [open, setOpen] = useState(false);
  const pair = `${latest.cand.label} vs ${latest.base.label}`;
  return (
    <Stack direction="row" alignItems="center" sx={{ flexShrink: 0 }}>
      <CustomTooltip show title={latest.tip} size="small" placement="top">
        <Button
          component={RouterLink}
          to={latest.href}
          size="small"
          color="primary"
          data-testid="latest-vs-baseline"
          aria-label={`Latest vs baseline: ${pair}`}
          sx={{ whiteSpace: "nowrap", height: 28, px: 1, minWidth: 0 }}
        >
          {`Latest ${pair}`}
        </Button>
      </CustomTooltip>
      {latest.soFar ? (
        <>
          <Button
            ref={anchorRef}
            size="small"
            color="primary"
            aria-label="More latest pairs"
            aria-haspopup="menu"
            aria-expanded={open}
            onClick={() => setOpen(true)}
            data-testid="latest-vs-baseline-menu"
            sx={{ height: 28, minWidth: 0, px: 0.25 }}
          >
            <Iconify icon="mdi:chevron-down" width={16} />
          </Button>
          <Menu
            open={open}
            anchorEl={anchorRef.current}
            onClose={() => setOpen(false)}
          >
            <MenuItem
              component={RouterLink}
              to={latest.href}
              onClick={() => setOpen(false)}
              sx={{ typography: "s2" }}
            >
              {`Open ${pair}`}
            </MenuItem>
            <MenuItem
              component={RouterLink}
              to={latest.soFar.href}
              onClick={() => setOpen(false)}
              data-testid="latest-so-far"
              sx={{ typography: "s2" }}
            >
              {`Compare ${latest.soFar.label} so far`}
            </MenuItem>
          </Menu>
        </>
      ) : null}
    </Stack>
  );
}
LatestChip.propTypes = { latest: PropTypes.object.isRequired };

/** The answer's height with its two lines (reserved while loading). */
const ANSWER_MIN_HEIGHT = 72;
/** IT-R5 G2 P1: the longest `Reading the runs…` ever shows. */
export const READING_MS = 1000;

const plainTasks = (n, noun) => `${n} ${n === 1 ? noun : `${noun}s`}`;

/** ` (21%)` from a run's own progress; empty when it has none. */
const progressPct = (run) => {
  // W1-6: the share its numbers cover (the one ledger via partialProgress).
  const p = partialProgress(run);
  const done = Number(p ? p.done : run?.progress?.done);
  const total = Number(p ? p.total : run?.progress?.total);
  return total > 0 && Number.isFinite(done)
    ? ` (${Math.round((100 * done) / total)}%)`
    : "";
};

LeaderboardAnswer.propTypes = {
  runs: PropTypes.array.isRequired,
  allRuns: PropTypes.array,
  numbers: PropTypes.object,
  baselineRun: PropTypes.object,
  datasetId: PropTypes.string,
  datasetKind: PropTypes.string,
  fromUrl: PropTypes.string,
  onCompareAny: PropTypes.func,
  compareAnyOn: PropTypes.bool,
  selectedIds: PropTypes.instanceOf(Set),
  pinnedRunId: PropTypes.string,
  rowLabels: PropTypes.object,
};
