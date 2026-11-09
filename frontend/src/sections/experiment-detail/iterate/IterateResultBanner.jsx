// R2 (L2) · the auto-compare banner (R2-SPEC §4.3.2): after an iteration
// lands on `…/compare/<tab>?runs=<parent>,<new>&baseline=<parent>&from=iterate`
// it says in one sentence what moved, shows the chips, and nudges the user
// to rerun the rows that were carried over.
import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { compareRuns } from "src/sections/common/Runs/runNumbers";
import {
  Box,
  Button,
  Chip,
  IconButton,
  Link,
  Popover,
  Stack,
  Typography,
  useMediaQuery,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import { useLocation, useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { enqueueSnackbar } from "src/components/snackbar";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { useIterate, useMatrix, useRunConfigs } from "src/api/develop/r2";
import ConfigDiffStrip from "src/sections/common/Runs/ConfigDiffStrip";
import { runLabel } from "src/sections/common/Runs/runLabel";
import { canRerunHere, runKindOf } from "src/sections/common/Runs/runKind";
import { useRunPaths } from "src/api/develop/r4";
import { pathText } from "src/sections/common/Trajectory/pathModel";
import Term from "src/sections/common/ux/Term";
import { agentVersionOf } from "src/sections/common/Runs/provenanceModel";
import { useRootCauses } from "../matrix/useMatrixData";
import { useIterateStore } from "./iterateStore";
import { bannerPairOf } from "./bannerPair";
import {
  candidatePending,
  failurePathsKnown,
  fixVerdict,
  fullRunVerdict,
  pathFailMove,
  ranAsIs,
  scoredScope,
  successMove,
  triesText,
} from "./fixVerdict";
import usePagePair from "../paths/usePagePair";
import useQuickTests from "./useQuickTests";
import {
  costChip,
  pText,
  passKChip,
  signSentence,
  stillFailingRows,
  summarizeIteration,
  verdictWord,
  pPercent,
} from "./iterateSummary";
import {
  buildIterateBody,
  costPerAttempt,
  fmtAbout,
  fmtUsd,
  presetOf,
  previewOf,
} from "./iterateModel";
import PublishAndPin from "./PublishAndPin";
import { coverageOf, publishGateOf } from "./publishGate";
import { fullRunDoorOf } from "./fullRunDoor";
import {
  leftOutOfVerdict,
  settleUnscoredPartial,
  unscoredOf,
  verdictLeftOut,
} from "./unscoredModel";
import { useRunResults } from "src/api/develop/run-results";
import { resultsGridHref } from "src/sections/common/Analysis/analysisLinks";
import { rowsPill } from "src/sections/common/Results/filterPills";
import { pollIntervalFor } from "src/sections/common/Runs/runStatus";
import { runModelOf } from "src/sections/common/RunSources/rerunWhere";

/** The model a run calls (the dock's `fromModel` / `toModel` rule). */
const modelOfRun = (run) =>
  run?.config?.harness?.model ?? runModelOf(run?.config ?? {}) ?? undefined;

/** Kinds whose tries run on the user's machine (the run waits for them). */
const WAITS_FOR_CODE = new Set(["code", "upload"]);

const TEST_NAME =
  "Two-sided sign test on the rows whose pass rate changed (McNemar's exact test on paired per-row outcomes). Ties are dropped; significant at p < 0.05.";

// Same chip as ConfigDiffStrip's, so the banner's chips read as one row.
const chipSx = {
  height: 22,
  borderRadius: 0.5,
  typography: "s2",
  fontWeight: 500,
  color: "text.primary",
  bgcolor: "background.paper",
  "& .MuiChip-label": { px: 0.75 },
};

/** `Fixed 6 of 13 failing rows · 7 still fail · 17 carried over unchanged · 0 got worse`. */
export function fixSentence(matrix, { baseId, candId, stillFailing }) {
  const rerun = compareRuns({
    matrix,
    baselineId: baseId,
    candidateId: candId,
    scope: "rerun",
  });
  const all = compareRuns({ matrix, baselineId: baseId, candidateId: candId });
  if (rerun.partial)
    return `Partial · ${rerun.partial.scored} of ${rerun.partial.total} tries scored · verdict when done`;
  const failing = rerun.better + stillFailing;
  return [
    `Fixed ${rerun.better} of ${failing} failing ${failing === 1 ? "row" : "rows"}`,
    `${stillFailing} still fail`,
    `${all.carried} carried over unchanged`,
    `${rerun.worse} got worse`,
  ].join(" · ");
}

export const BANNER_FOLD_MS = 6000;

/**
 * IT-R5 G3 (P4): `Fix the next failing step` → the side by side page on
 * the first row that still fails (new run vs its base), where the lane
 * shows the first wrong step and its `Fix this step`. Null when none. Pure.
 */
export function nextFixHrefOf({ datasetId, baseId, candId, stillFailing }) {
  const rowId = (stillFailing ?? [])[0];
  if (!datasetId || !baseId || !candId || !rowId) return null;
  const q = new URLSearchParams({
    runs: `${baseId},${candId}`,
    baseline: baseId,
    cand: candId,
    row: rowId,
  });
  return `/dashboard/develop/${datasetId}/compare/tasks?${q.toString()}`;
}

const IterateResultBanner = ({
  datasetId,
  experimentId,
  runIds,
  baselineId,
}) => {
  const { pathname, search } = useLocation();
  const navigate = useNavigate();
  const { role } = useAuthContext();
  const canEdit = Boolean(RolePermission.DATASETS[PERMISSIONS.UPDATE][role]);
  const sp = new URLSearchParams(search);
  // iterate r1 G3 (P2): the fix-run that just finished shows here too (on
  // the page it started from, or any page with both runs) — the run never
  // navigates on its own. r2 (P2): one pair rule, shared with the results
  // verdict (bannerPairOf), so the page never shows two verdicts.
  const last = useIterateStore((s) => s.lastFinished);
  const dismissedList = useIterateStore((s) => s.dismissed);
  const pair = bannerPairOf({
    pathname,
    search,
    datasetId,
    runIds,
    baselineId,
    last,
  });
  const lastHere = pair?.last ?? null;
  const baseId = pair?.baseId ?? null;
  const candId = pair?.candId ?? null;
  const show = Boolean(baseId && candId);

  const dismissed = dismissedList;
  const finished = useIterateStore((s) => s.finished[candId] ?? null);
  // options-truth L3 G10 (banner:run-the-other-n): the guard run that
  // reran the other rows of this new run, once it finished.
  const guardDoneId = useIterateStore(
    (s) =>
      Object.entries(s.finished).find(
        ([, f]) => candId && f?.guardFor === candId,
      )?.[0] ?? null,
  );
  const guardDoneLabel = useIterateStore((s) =>
    guardDoneId ? s.finished[guardDoneId]?.label ?? null : null,
  );
  const active = useIterateStore((s) => s.active);
  const start = useIterateStore((s) => s.start);
  const dismiss = useIterateStore((s) => s.dismissBanner);
  const [howAnchor, setHowAnchor] = useState(null);
  // P2: open on the first view, its first line after that (remembered per
  // run; the chevron toggles).
  const fold = useIterateStore((s) => (candId ? s.bannerFold[candId] : null));
  const setFold = useIterateStore((s) => s.setBannerFold);
  const [firstView, setFirstView] = useState(null);
  // Rule 10: a laptop window (< 820 px high) starts on the first line too,
  // so the grid keeps the screen; the chevron opens the details.
  const short = useMediaQuery("(max-height:819.95px)");
  useEffect(() => {
    if (!candId || fold) return;
    if (!short) setFirstView(candId);
    setFold(candId, "closed");
  }, [candId, fold, setFold, short]);
  const open = firstView === candId || fold === "open";
  // IT-R5 G3 (P4): the first view folds to its one line after 6 s, so the
  // grid gets the screen back (the chevron reopens it).
  useEffect(() => {
    if (!candId || firstView !== candId) return undefined;
    const t = setTimeout(() => setFirstView(null), BANNER_FOLD_MS);
    return () => clearTimeout(t);
  }, [candId, firstView]);

  // v2.1 g4: set once the verdict is settled (skipped tries excluded), so
  // polling stops even though those tries never get a score.
  const settledRef = useRef(null);
  const matrix = useMatrix(
    { datasetId, runIds: show ? [baseId, candId] : [], baselineId: baseId },
    {
      enabled: show,
      // options-truth L3 G10 (banner:verdict): poll while the new run's
      // tries are still scoring, at an active run's cadence, so the verdict
      // arrives on its own.
      refetchInterval: (q) =>
        settledRef.current !== candId &&
        candidatePending(q.state.data?.data?.result, baseId, candId)
          ? pollIntervalFor({ status: "running" })
          : false,
    },
  );
  const configs = useRunConfigs(datasetId, { enabled: show });
  // Quick tests are unlisted: R1 returns them only with include=quick_tests.
  const quick = useQuickTests(show ? datasetId : null);
  const runs = useMemo(
    () => [...(configs.data?.results ?? []), ...(quick.data ?? [])],
    [configs.data, quick.data],
  );
  const base = runs?.find((r) => r.run_id === baseId) ?? null;
  const cand = runs?.find((r) => r.run_id === candId) ?? null;
  const iterate = useIterate();

  // the interface contract: both runs' own cells (skipped / N/A / score error); review
  // M4: rows whose primary eval has no score in either run are left out of
  // every verdict count (unscoredModel.verdictLeftOut).
  const pairResults = useRunResults(
    datasetId,
    { runIds: show ? [baseId, candId] : [], baselineId: baseId },
    { enabled: show },
  );
  const leftOut = useMemo(
    () => verdictLeftOut(pairResults.data, [baseId, candId]).rowIds,
    [pairResults.data, baseId, candId],
  );
  const s = useMemo(
    () =>
      matrix.data && !pairResults.isLoading
        ? summarizeIteration(matrix.data, baseId, candId, { exclude: leftOut })
        : null,
    [matrix.data, pairResults.isLoading, baseId, candId, leftOut],
  );
  // The success move over every row both ran: runNumbers (the headline's).
  const cmpAll = useMemo(
    () =>
      matrix.data
        ? compareRuns({
            matrix: matrix.data,
            baselineId: baseId,
            candidateId: candId,
          })
        : null,
    [matrix.data, baseId, candId],
  );
  // … and over the rows a score backs (all of them unless some are left out)
  const cmpScored = useMemo(
    () =>
      matrix.data && leftOut.length
        ? compareRuns({
            matrix: matrix.data,
            baselineId: baseId,
            candidateId: candId,
            scope: scoredScope(matrix.data, leftOut),
          })
        : cmpAll,
    [matrix.data, baseId, candId, leftOut, cmpAll],
  );
  // What the Paths tab shows by default (the page's candidate).
  const pagePair = usePagePair({
    datasetId: show ? datasetId : null,
    experimentId,
    runIds,
    baselineId,
    search,
  });
  // P2: the failure the fix aimed at (its rows), from the run that started
  // it, else the finding R5 names for the base run.
  const causes = useRootCauses(
    { datasetId, runIds, baselineId },
    { enabled: show && (runIds ?? []).length > 1 },
  );
  const basePaths = useRunPaths(baseId, { enabled: show });
  const candPaths = useRunPaths(candId, { enabled: show });

  // IT-R5 G3 (P4): a new run started → the previous result is stale; the
  // dock bar says what is running until its own result arrives.
  const stale = Boolean(active?.runId && active.runId !== candId);
  if (
    !show ||
    stale ||
    dismissed.includes(candId) ||
    !s ||
    !s.rows ||
    !base ||
    !cand
  )
    return null;

  const baseLabel = runLabel(base);
  const candLabel = runLabel(cand);
  // Partial = the new run carried rows over from this baseline; rows a
  // chain carried from an intermediate run (#2G from #2F) count as run.
  const carriedFromBase = (matrix.data.rows ?? []).filter(
    (r) => r.cells?.[candId]?.carried_from === baseLabel,
  );
  const partial = carriedFromBase.length > 0 || cand.listed === false;
  const unscored = unscoredOf(pairResults.data?.rows, candId, {
    carried: new Set(
      (matrix.data.rows ?? [])
        .filter((r) => r.cells?.[candId]?.carried_from)
        .map((r) => r.row_id),
    ),
    nameOf: (k) =>
      (pairResults.data?.summary?.metrics ?? []).find((m) => m.key === k)
        ?.name ?? k,
    leftOut: leftOutOfVerdict(s, { partial }),
  });
  // R4 implementation (8b/8e): a rerun reads `Fixed 6 of 13 failing rows · 7 still
  // fail · 17 carried over unchanged · 0 got worse`, from runNumbers (the
  // same numbers as the Compare chain and the run view headline).
  // v2.1 g4: tries whose primary eval was skipped never get a score; once
  // they are the only ones missing, the run is done (no `Partial · …`).
  const rerunCmp = settleUnscoredPartial(
    compareRuns({
      matrix: matrix.data,
      baselineId: baseId,
      candidateId: candId,
      scope: "rerun",
    }),
    matrix.data,
    pairResults.data?.rows,
    candId,
    verdictLeftOut(pairResults.data, [baseId, candId]).keyOf[candId],
  );
  settledRef.current = rerunCmp?.partial ? null : candId;
  const labelById = new Map(
    (matrix.data.rows ?? []).map((r) => [r.row_id, r.label ?? `Row ${r.n}`]),
  );
  const labelOf = (id) => labelById.get(id) ?? id;
  const cellsById = new Map(
    (matrix.data.rows ?? []).map((r) => [r.row_id, r.cells ?? {}]),
  );
  const movesOf = (id) => {
    const c = cellsById.get(id);
    return c ? `${triesText(c[baseId])} → ${triesText(c[candId])}` : null;
  };
  const causeId = finished?.cause?.id ?? cand.cause_id ?? null;
  const preset = presetOf(null, causeId);
  const causeTitle = finished?.cause?.title ?? preset?.cause ?? null;
  // P2 (a): the rows of the failure the fix aimed at — what the dock ran
  // (`cause.rows`), else the finding R5 names for the base run.
  const finding = causeId
    ? (causes.data?.causes ?? []).find(
        (c) => c.id === causeId && (c.run_id === baseId || !c.run_id),
      ) ?? (causes.data?.causes ?? []).find((c) => c.id === causeId)
    : null;
  const target =
    finished?.cause?.rows ??
    (finding?.rows ?? []).map((r) => r.row_id).filter(Boolean);
  // P2 (b): a full run is about every row (never `Nothing to fix`).
  // P3: rows the fix did not execute are `not re-checked`, never `0 broke`.
  const carriedEarly = rerunCmp.partial ? null : cmpAll?.carried ?? 0;
  // options-truth L3 G10 (banner:verdict): a rerun with nothing changed
  // (every run back to the base has `changes: []`) never says `Fixed`.
  // A code / upload run's code lives on the user's machine: an empty
  // `changes` list there says nothing about what they edited.
  const asIs =
    !WAITS_FOR_CODE.has(runKindOf(cand)) && ranAsIs(runs, candId, baseId);
  const verdict = partial
    ? fixVerdict(s, {
        target: target.length ? target : null,
        fixedText: finished?.cause?.fixed ?? preset?.fixed ?? null,
        labelOf,
        notRechecked: carriedEarly ?? 0,
        noChange: asIs,
        from: baseLabel,
      })
    : fullRunVerdict(cmpScored, {
        name: agentVersionOf(cand)?.version ?? candLabel,
        noise: verdictWord(s.sign),
        labelOf,
        movesOf,
        total: s.rows,
        leftOut: s.unscored.length,
      });
  const brokeRows = partial ? verdict.brokeRows : verdict.worseRows;
  // Still scoring: say so instead of a verdict.
  const sentence = rerunCmp.partial
    ? `Partial · ${rerunCmp.partial.scored} of ${rerunCmp.partial.total} tries scored · verdict when done`
    : verdict.text;
  // The verdict already counts fixed / still failing; the detail says
  // what else moved (carried rows, or the full run's better / worse).
  const carried = rerunCmp.partial ? null : cmpAll?.carried ?? 0;
  // (A full run's headline already says better / worse / same.)
  // (The verdict already says `24 rows not re-checked`; the detail names
  // where their results come from.)
  const detail =
    partial && carried
      ? `The other ${carried} rows' results are carried from ${baseLabel}`
      : null;
  const pathId =
    finished?.pathId ?? (lastHere?.runId === candId ? lastHere.pathId : null);
  // options-truth L3 G10: the failing-path line only when the new run has
  // path data (a run with no traces has no path to compare).
  // While its tries still score, the move would compare against nothing.
  const pathMove =
    !rerunCmp.partial &&
    failurePathsKnown(candPaths.data, s.stillFailing?.length ?? 0)
      ? pathFailMove(basePaths.data, candPaths.data, pathId)
      : null;
  const move = successMove(cmpScored);
  const pathsHref = `/dashboard/develop/${datasetId}/compare/paths?runs=${baseId},${candId}&baseline=${baseId}&cand=${candId}&prun=${candId}&pcmp=${baseId}&from=iterate`;
  const compareHref =
    lastHere?.runId === candId && lastHere.compareUrl
      ? lastHere.compareUrl
      : `/dashboard/develop/${datasetId}/compare/data?runs=${baseId},${candId}&baseline=${baseId}&from=iterate`;
  const onThisCompare =
    /\/compare\/(data|matrix)\/?$/.test(pathname) &&
    (runIds ?? []).length === 2 &&
    (runIds ?? []).includes(baseId) &&
    (runIds ?? []).includes(candId);
  const onPaths = /\/paths\/?$/.test(pathname);
  const onThesePaths =
    onPaths && (sp.get("prun") ?? pagePair.candidateId) === candId;
  // One primary next step: not fixed → see where it still fails (Paths);
  // fixed → compare the two runs row by row.
  const pathsPrimary = partial && verdict.word !== "Fixed";
  const toneColor =
    !rerunCmp.partial && verdict.tone === "good"
      ? "green.600"
      : !rerunCmp.partial && verdict.tone === "bad"
        ? "red.600"
        : !rerunCmp.partial && verdict.tone === "mixed"
          ? "amber.500"
          : "primary.main";

  const kChip = passKChip(s);
  const cost = costChip(s);
  // options-truth L3 G10 (banner:verdict): while tries are still scoring
  // the chips are the numbers so far, never a final verdict.
  const soFar = rerunCmp.partial ? " so far" : "";
  // Nothing scored yet: no numbers to show (never `100% → 100%`).
  const noneScored = Boolean(rerunCmp.partial) && !rerunCmp.partial.scored;
  const tab = pathname.endsWith("/matrix") ? "matrix" : "data";
  const summaryUrl = `/dashboard/develop/${datasetId}/compare/summary${search}`;

  // Guard: rerun the carried rows from the new run, compared with the base.
  const guardRows = carriedFromBase.map((r) => r.row_id);
  const guardTrials = cand.trials ?? cand.config?.trials ?? s.k ?? 1;
  const guardKind = runKindOf(cand);
  // options-truth L3 G10: priced as the dock prices `Also verify the other
  // N rows`: the base run's cost per call scaled to the model the new run
  // calls (a harness change to gpt-4o prices at gpt-4o).
  const guardPreview = previewOf({
    rows: guardRows.length,
    trials: guardTrials,
    kind: guardKind,
    costPerAttemptUsd: costPerAttempt(matrix.data, baseId),
    key: baseId,
    fromModel: modelOfRun(base),
    toModel: modelOfRun(cand),
    // v2.1 g4: and its max tokens (the engine caps each reply there).
    fromMaxTokens:
      base?.config?.model?.params?.max_tokens ??
      base?.config?.model?.params?.maxTokens ??
      null,
    toMaxTokens:
      cand?.config?.model?.params?.max_tokens ??
      cand?.config?.model?.params?.maxTokens ??
      null,
    // options-truth L5 G2: the dock's `Also verify` rule: the new run's
    // tool modes and per-try cap move the price the same way.
    fromTools: base?.config?.tools ?? [],
    toTools: cand?.config?.tools ?? [],
    capUsd:
      cand?.config?.harness?.max_cost_usd ??
      cand?.config?.harness?.budget?.max_cost_usd ??
      null,
  });
  const guardRowWord = guardRows.length === 1 ? "row" : "rows";
  const guardLabel = `Run ${guardRows.length} ${guardRowWord} · ≈ ${fmtUsd(guardPreview.usd)} · ${fmtAbout(guardPreview.seconds)}`;
  const guardShort = `Run the other ${guardRows.length} (≈ ${fmtUsd(guardPreview.usd)})`;
  const guardStartMeta = (result) => ({
    datasetId,
    baseId,
    startedPath: pathname,
    // Compared with the root, not the intermediate run.
    compareUrl: `/dashboard/develop/${datasetId}/compare/${tab}?runs=${baseId},${result.run_id}&baseline=${baseId}&from=iterate`,
    cause: null,
    noise: Boolean(result.script?.noise),
    guardFor: candId,
  });
  const runGuard = () => {
    const body = buildIterateBody({
      parentRunId: candId,
      quickTest: false,
      scopeType: "rows",
      rowIds: guardRows,
      scopeLabel: `Rows carried over from ${baseLabel}: ${guardRows.length}`,
      trials: guardTrials,
      carryOver: true,
      changes: [],
      saveAs: null,
      causeId: null,
      from: tab === "matrix" ? "matrix" : undefined,
    });
    iterate.mutate(
      {
        experimentId: cand.experiment_id,
        datasetId,
        ...body,
      },
      {
        onSuccess: (result) => start(result, guardStartMeta(result)),
        onError: (err) =>
          enqueueSnackbar(
            `Couldn't start the iteration: ${getRequestErrorMessage(err, "try again")}`,
            { variant: "error" },
          ),
      },
    );
  };
  const guardBusy = iterate.isPending || active?.guardFor === candId;
  // P2: a row that broke opens in the row panel (this Data tab when it
  // shows both runs, else the pair's compare page).
  const openRow = (rowId) => {
    const here =
      /\/data\/?$/.test(pathname) &&
      (runIds ?? []).includes(baseId) &&
      (runIds ?? []).includes(candId);
    if (here) {
      const next = new URLSearchParams(search);
      next.set("row", rowId);
      next.set("run", candId);
      navigate(`${pathname}?${next.toString()}`);
    } else navigate(`${compareHref}&row=${rowId}&run=${candId}`);
  };
  // dataset integration FE-0: only a run Future AGI can start again itself gets the
  // guard; a code or uploaded run's rows ran from a generated runner.
  const guardShown =
    partial &&
    cand.listed !== false &&
    canRerunHere(cand) &&
    guardRows.length > 0 &&
    !guardDoneId;
  // The guard finished: its verdict (every row, vs the base) is one click
  // away; the prompt to run the other rows is gone (it ran).
  const guardDoneHref = guardDoneId
    ? `/dashboard/develop/${datasetId}/compare/${tab}?runs=${baseId},${guardDoneId}&baseline=${baseId}&from=iterate`
    : null;
  const nextFixHref = rerunCmp.partial
    ? null
    : nextFixHrefOf({
        datasetId,
        baseId,
        candId,
        stillFailing: stillFailingRows(matrix.data, baseId, candId),
      });

  const tone = rerunCmp.partial ? "none" : verdict.tone;
  const icon =
    tone === "good"
      ? "mdi:check-circle"
      : tone === "bad"
        ? "mdi:close-circle"
        : tone === "mixed"
          ? "mdi:alert-circle"
          : "mdi:source-branch-check";
  // IT-R4 G3 (P3): `within noise` on hover: `2 of 30 tasks got worse; 7
  // better vs 2 worse could be chance (sign test p = 18%, needs < 5%)`.
  const noiseAt = sentence.indexOf("within noise");
  const noiseBasis = cmpAll
    ? `${cmpAll.worse} of ${s.rows} tasks got worse; ${cmpAll.better} better vs ${cmpAll.worse} worse could be chance (sign test p = ${pPercent(s.sign?.p)}, needs < 5%).`
    : "";
  // IT-R4 G3 (P3): a full rerun of a draft version ends with publish.
  // the interface contract (BL-06): one gate with the dock (publishGate.js): until the verdict
  // is in, Publish stays on screen, disabled, and says why.
  const draftVersion = agentVersionOf(cand);
  const gate = publishGateOf({
    rerunCmp,
    carriedFromBase,
    ...coverageOf(matrix.data, baseId, candId),
    draftVersion,
    cand,
    baseLabel,
    candLabel,
    canEdit,
  });
  const canPublish = gate.show && gate.ready;
  const worseNames = (brokeRows ?? []).map(labelOf);
  const outcome = cmpAll
    ? `${cmpAll.better} ${cmpAll.better === 1 ? "task" : "tasks"} got better, ${cmpAll.worse} got worse${
        worseNames.length
          ? ` (${worseNames.join(", ")}${verdictWord(s.sign) === "significant" ? "" : ", within noise"})`
          : ""
      }.`
    : "";
  const gatedPublish = gate.show ? (
    <PublishAndPin
      datasetId={datasetId}
      cand={cand}
      candLabel={candLabel}
      baseId={baseId}
      baseLabel={baseLabel}
      version={draftVersion.version}
      outcome={outcome}
      disabled={!gate.ready}
      reason={gate.reason}
      gate={gate.state}
    />
  ) : null;
  const publishButton = canPublish ? gatedPublish : null;
  // HO S7: rows left out or a quick test: `Run every row on v3.1` opens the
  // dock on the run the fix started from, scope All, the fix's edits.
  const fullRun = fullRunDoorOf({
    gate,
    cand,
    versionName: draftVersion?.version,
    path: `${pathname}${search}`,
  });
  const fullRunButton = fullRun ? (
    <Button
      size="small"
      variant="outlined"
      data-testid="iterate-banner-full-run"
      startIcon={<Iconify icon="mdi:play-circle-outline" width={16} />}
      onClick={() => {
        useIterateStore.getState().patchDraft(fullRun.baseId, fullRun.draft);
        navigate(fullRun.href);
      }}
      sx={{ whiteSpace: "nowrap", flexShrink: 0 }}
    >
      {fullRun.label}
    </Button>
  ) : null;
  const pathsButton = (
    <Button
      size="small"
      variant={pathsPrimary ? "contained" : "outlined"}
      onClick={() => {
        if (onPaths && (runIds ?? []).includes(candId)) {
          const next = new URLSearchParams(search);
          next.set("prun", candId);
          next.set("pcmp", baseId);
          navigate(`${pathname}?${next.toString()}`, { replace: true });
        } else navigate(pathsHref);
      }}
      startIcon={<Iconify icon="mdi:source-branch" width={16} />}
      data-testid="iterate-banner-paths"
      sx={{ whiteSpace: "nowrap", flexShrink: 0 }}
    >
      {`Open Paths for ${candLabel}`}
    </Button>
  );
  const compareButton = (
    <Button
      size="small"
      variant={
        !publishButton && (!pathsPrimary || onThesePaths)
          ? "contained"
          : "outlined"
      }
      onClick={() => navigate(compareHref)}
      startIcon={<Iconify icon="mdi:compare-horizontal" width={16} />}
      data-testid="iterate-banner-compare"
      sx={{ whiteSpace: "nowrap", flexShrink: 0 }}
    >
      {`Compare ${candLabel} with ${baseLabel}`}
    </Button>
  );
  const guardButton = (
    <CustomTooltip
      show={!canEdit}
      title="Editors can start runs"
      size="small"
      arrow
    >
      <span>
        <Button
          size="small"
          variant={open ? "outlined" : "contained"}
          color={open ? "inherit" : "primary"}
          disabled={!canEdit || guardBusy}
          onClick={runGuard}
          startIcon={<Iconify icon="mdi:play" width={16} />}
          data-testid="iterate-banner-guard"
          sx={{
            typography: "s2",
            fontWeight: 600,
            whiteSpace: "nowrap",
            ...(open && { bgcolor: "background.paper" }),
          }}
        >
          {open ? guardLabel : guardShort}
        </Button>
      </span>
    </CustomTooltip>
  );
  // One primary action on the first line: the rows left to check (a
  // partial run), else the row that got worse, else Compare.
  const firstLineActions = (() => {
    if (rerunCmp.partial) return gatedPublish;
    if (publishButton) return publishButton;
    if (fullRunButton) return fullRunButton;
    if (!open && guardShown) return guardButton;
    if (onThisCompare) return onThesePaths ? null : pathsButton;
    return pathsPrimary && !onThesePaths ? pathsButton : compareButton;
  })();

  return (
    <Box
      data-testid="iterate-banner"
      data-open={open ? "true" : "false"}
      data-tone={tone}
      sx={(theme) => ({
        mx: 1,
        px: 1.5,
        py: open ? 1 : 0.25,
        // Collapsed: one line (about 33 px at 1280 x 720); its buttons
        // shrink to 26 px so the line never grows.
        ...(!open && {
          "& .MuiButton-root": { height: 26, py: 0 },
          "& .MuiIconButton-root": { p: 0.25 },
        }),
        border: "1px solid",
        borderColor: alpha(theme.palette.primary.main, 0.24),
        bgcolor: alpha(theme.palette.primary.main, 0.04),
        borderRadius: 1,
      })}
    >
      <Stack
        direction="row"
        alignItems={open ? "flex-start" : "center"}
        gap={1}
      >
        <Iconify
          icon={icon}
          width={18}
          data-testid="iterate-banner-icon"
          sx={{ color: toneColor, mt: open ? 0.25 : 0, flexShrink: 0 }}
        />
        <Stack spacing={0.75} sx={{ flex: 1, minWidth: 0 }}>
          <Stack
            direction="row"
            alignItems="center"
            gap={1}
            flexWrap={open ? "wrap" : "nowrap"}
            sx={{ rowGap: 0.75, minWidth: 0 }}
          >
            <CustomTooltip
              show={!open}
              title={`${candLabel} vs ${baseLabel} · ${sentence}`}
              size="small"
              arrow
            >
              <Typography
                typography="s1"
                fontWeight={600}
                noWrap={!open}
                data-testid="iterate-banner-sentence"
                sx={{ minWidth: 0, flex: open ? "1 1 320px" : "0 1 auto" }}
              >
                {noiseAt >= 0 ? (
                  <>
                    {`${candLabel} vs ${baseLabel} · ${sentence.slice(0, noiseAt)}`}
                    {/* IT-R4 G3 (P3): `within noise` says its basis. */}
                    <CustomTooltip show size="small" arrow title={noiseBasis}>
                      <Box
                        component="span"
                        data-testid="iterate-banner-noise"
                        sx={{
                          textDecoration: "underline dotted",
                          textUnderlineOffset: 3,
                          cursor: "help",
                        }}
                      >
                        within noise
                      </Box>
                    </CustomTooltip>
                    {sentence.slice(noiseAt + "within noise".length)}
                  </>
                ) : (
                  `${candLabel} vs ${baseLabel} · ${sentence}`
                )}
              </Typography>
            </CustomTooltip>
            {/* P2: rows that broke are always named with a link. */}
            {!rerunCmp.partial &&
              brokeRows.slice(0, 2).map((id) => (
                <Link
                  key={id}
                  component="button"
                  type="button"
                  typography="s2"
                  fontWeight={600}
                  underline="always"
                  onClick={() => openRow(id)}
                  data-testid="iterate-banner-open-row"
                  sx={{ whiteSpace: "nowrap", flexShrink: 0 }}
                >
                  {`Open ${labelOf(id)}`}
                </Link>
              ))}
            {/* IT-R5 G3 (P4): rows still fail → the next fix starts at
                the first one's first wrong step (the side by side page). */}
            {nextFixHref ? (
              <Link
                component="button"
                type="button"
                typography="s2"
                fontWeight={600}
                underline="always"
                onClick={() => navigate(nextFixHref)}
                data-testid="iterate-banner-next-fix"
                sx={{ whiteSpace: "nowrap", flexShrink: 0 }}
              >
                Fix the next failing step
              </Link>
            ) : null}
            {/* the interface contract: rows the new run left unscored, linked. */}
            {!rerunCmp.partial && unscored.text ? (
              <Link
                component="button"
                type="button"
                typography="s2"
                underline="always"
                color="warning.dark"
                onClick={() =>
                  navigate(
                    resultsGridHref({
                      mode: "compare",
                      datasetId,
                      runIds: [baseId, candId],
                      baselineId: baseId,
                      pills: [
                        rowsPill(unscored.linkIds, { label: "Not scored" }),
                      ],
                      from: "iterate",
                    }),
                  )
                }
                data-testid="iterate-banner-unscored"
                title={unscored.text}
                sx={{
                  whiteSpace: "nowrap",
                  // the verdict keeps its width: this line takes only the
                  // room left (basis 0, up to its own width), never the
                  // verdict's
                  flex: "1000 1 0",
                  maxWidth: "max-content",
                  minWidth: 120,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {unscored.text}
              </Link>
            ) : null}
            {!open ? <Box sx={{ flex: 1 }} /> : null}
            <Stack
              direction="row"
              alignItems="center"
              gap={0.75}
              sx={{ flexShrink: 0 }}
            >
              {open && !rerunCmp.partial ? (
                <>
                  {!onThesePaths ? pathsButton : null}
                  {!onThisCompare ? compareButton : null}
                  {gatedPublish}
                  {fullRunButton}
                </>
              ) : (
                firstLineActions
              )}
            </Stack>
          </Stack>
          {open ? (
            <>
              {/* One wrapped line: what else moved · the failing path · the
                  finding it confirmed. */}
              <Typography
                typography="s2"
                color="text.secondary"
                data-testid="iterate-banner-detail"
                sx={{ fontVariantNumeric: "tabular-nums" }}
              >
                {detail && carried ? (
                  <>
                    {`The other ${carried} rows' results are `}
                    <Term k="carried">carried over</Term>
                    {` from ${baseLabel} (not re-run)`}
                  </>
                ) : null}
                {pathMove ? (
                  <Box
                    component="span"
                    data-testid="iterate-banner-path"
                    sx={{ color: "text.primary" }}
                  >
                    {`${detail ? " · " : ""}Most common failing path: ${pathMove.from} failing ${pathMove.from === 1 ? "try" : "tries"} → ${pathMove.to} `}
                    <Box
                      component="span"
                      sx={{
                        fontFamily: "monospace",
                        typography: "s3",
                        color: "text.secondary",
                      }}
                    >
                      {`(${pathText(pathMove.path.steps)})`}
                    </Box>
                  </Box>
                ) : null}
                {causeTitle && verdict.fixed > 0
                  ? ` · Cause "${causeTitle}" confirmed by rerun.`
                  : null}
              </Typography>
              <Stack
                direction="row"
                alignItems="center"
                gap={0.75}
                flexWrap="wrap"
              >
                {move && !noneScored ? (
                  <CustomTooltip show title="Open Summary" size="small" arrow>
                    <Chip
                      size="small"
                      variant="outlined"
                      clickable
                      onClick={() => navigate(summaryUrl)}
                      sx={chipSx}
                      data-testid="iterate-banner-pass1"
                      label={
                        <span>
                          <Term k="passAt1">pass@1</Term>
                          {` · passed on the first try ${move}${soFar}`}
                        </span>
                      }
                    />
                  </CustomTooltip>
                ) : null}
                {kChip && !noneScored ? (
                  <CustomTooltip show title="Open Summary" size="small" arrow>
                    <Chip
                      size="small"
                      variant="outlined"
                      clickable
                      onClick={() => navigate(summaryUrl)}
                      sx={chipSx}
                      label={
                        <span>
                          <Term k="passHatK">{`pass^${s.k}`}</Term>
                          {` · passed all ${s.k} tries ${kChip.replace(/^pass\^\d+ /, "")}${soFar}`}
                        </span>
                      }
                    />
                  </CustomTooltip>
                ) : null}
                {cost && !noneScored ? (
                  <Chip
                    size="small"
                    variant="outlined"
                    label={`${cost}${soFar}`}
                    sx={chipSx}
                    data-testid="iterate-banner-cost"
                  />
                ) : null}
                <ConfigDiffStrip
                  baseline={baseId}
                  candidate={candId}
                  runs={runs}
                  compact
                />
                <Link
                  component="button"
                  type="button"
                  typography="s2"
                  color="text.secondary"
                  underline="always"
                  onClick={(e) => setHowAnchor(e.currentTarget)}
                >
                  How is this computed?
                </Link>
              </Stack>
              {guardDoneHref ? (
                <Stack
                  direction="row"
                  alignItems="center"
                  gap={1}
                  flexWrap="wrap"
                  data-testid="iterate-banner-guard-done"
                  sx={{ px: 1, py: 0.5 }}
                >
                  <Iconify
                    icon="mdi:check-circle-outline"
                    width={16}
                    sx={{ color: "text.secondary" }}
                  />
                  <Typography typography="s2" sx={{ flex: 1, minWidth: 240 }}>
                    {`${guardDoneLabel ?? "The guard run"} reran the other ${guardRows.length} ${guardRows.length === 1 ? "row" : "rows"} from ${candLabel}.`}
                  </Typography>
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={() => navigate(guardDoneHref)}
                    data-testid="iterate-banner-guard-compare"
                  >
                    {`Compare ${guardDoneLabel ?? "it"} with ${baseLabel}`}
                  </Button>
                </Stack>
              ) : null}
              {guardShown ? (
                <Stack
                  direction="row"
                  alignItems="center"
                  gap={1}
                  flexWrap="wrap"
                  sx={(theme) => ({
                    px: 1,
                    py: 0.5,
                    borderRadius: 0.75,
                    bgcolor: alpha(theme.palette.warning.main, 0.12),
                  })}
                >
                  <Iconify
                    icon="mdi:alert-outline"
                    width={16}
                    sx={{ color: "amber.500" }}
                  />
                  <Typography typography="s2" sx={{ flex: 1, minWidth: 240 }}>
                    {`You reran ${s.rerun.length} of ${s.rows} rows. Fixes often break rows that used to pass. Run the other ${guardRows.length} too?`}
                  </Typography>
                  {guardButton}
                </Stack>
              ) : null}
            </>
          ) : null}
        </Stack>
        <CustomTooltip
          show
          title={open ? "Show one line" : "Show the details"}
          size="small"
          arrow
        >
          <IconButton
            size="small"
            aria-label={open ? "Collapse the result" : "Expand the result"}
            aria-expanded={open}
            onClick={() => {
              setFirstView(null);
              setFold(candId, open ? "closed" : "open");
            }}
            data-testid="iterate-banner-fold"
            sx={{ mt: open ? -0.25 : 0 }}
          >
            <Iconify
              icon={open ? "mdi:chevron-up" : "mdi:chevron-down"}
              width={16}
            />
          </IconButton>
        </CustomTooltip>
        <CustomTooltip show title="Dismiss" size="small" arrow>
          <IconButton
            size="small"
            aria-label="Dismiss"
            onClick={() => dismiss(candId)}
            sx={{ mt: open ? -0.25 : 0 }}
          >
            <Iconify icon="mdi:close" width={16} />
          </IconButton>
        </CustomTooltip>
      </Stack>
      <Popover
        open={Boolean(howAnchor)}
        anchorEl={howAnchor}
        onClose={() => setHowAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        slotProps={{ paper: { sx: { p: 1.5, maxWidth: 380 } } }}
      >
        <Stack spacing={1}>
          <Typography typography="s2" fontWeight={500}>
            {signSentence(s)}
          </Typography>
          <Typography typography="s3" color="text.secondary">
            {`${TEST_NAME} ${pText(s.sign?.p)}.`}
          </Typography>
          <Typography typography="s3" color="text.secondary">
            {`pass@1 = passing attempts ÷ attempts over all ${s.rows} rows; pass^${s.k} = rows that pass all ${s.k} trials.`}
          </Typography>
        </Stack>
      </Popover>
    </Box>
  );
};

IterateResultBanner.propTypes = {
  datasetId: PropTypes.string,
  experimentId: PropTypes.string,
  runIds: PropTypes.arrayOf(PropTypes.string),
  runId: PropTypes.string,
  baselineId: PropTypes.string,
  trial: PropTypes.number,
  rowId: PropTypes.string,
};

export default IterateResultBanner;
