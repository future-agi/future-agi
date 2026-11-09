import React, { useCallback, useEffect, useMemo } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Link,
  Stack,
  Typography,
  useMediaQuery,
} from "@mui/material";
import {
  Link as RouterLink,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";
import Iconify from "src/components/iconify";
import { shareLinkOf, shareLinkSummary } from "./results/shareLink";
import { copyText } from "src/sections/common/ux/CopyButton";
import notify from "src/sections/common/ux/toast";
import { fmtCount } from "src/sections/common/format/fmt";
import { useDatasetRuns } from "src/api/develop/runs";
import { useDevelopDatasetList } from "src/api/develop/develop-detail";
import BackButton from "../develop-detail/Common/BackButton";
import ExperimentBar from "./ExperimentBar";
import RunResultsView from "./results/RunResultsView";
import useResultsUrlState, {
  MAX_COMPARE_RUNS,
} from "./results/useResultsUrlState";
import ExperimentAnalysisView from "./ExperimentSummary/Analysis/ExperimentAnalysisView";
import { useRunConfigs } from "src/api/develop/r2";
import { useCandidateRunId } from "src/api/develop/analysis";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { readR2Params } from "src/sections/common/Analysis/analysisLinks";
import ConfigDiffStrip from "src/sections/common/Runs/ConfigDiffStrip";
import { stripCandidate } from "src/sections/common/Runs/configDiff";
import MatrixView from "./matrix/MatrixView";
import PathsView from "./paths/PathsView"; // R4-F frozen line
import IterateButton from "./iterate/IterateButton";
import IterateDock from "./iterate/IterateDock";
import RunActionConfirmHost from "src/sections/develop-detail/Experiment/RunActionConfirmHost";
import IterateResultBanner from "./iterate/IterateResultBanner";
import LineageRibbon from "./iterate/LineageRibbon";
import CandidateSelect from "./results/CandidateSelect";
import {
  defaultCompare,
  pageCandidate as pickCandidate,
  versionTags,
} from "./results/pageCandidate";
import BaselinePicker from "src/sections/common/Runs/BaselinePicker";
import { useRunColors } from "src/sections/common/Runs/runColors";

/** Baseline on the compare page: URL → pinned if in runs → first run (C18). */
export function compareBaseline(
  runIds,
  requested,
  pinnedRunId,
  { allowNone = false } = {},
) {
  if (allowNone && requested === "none") return "none";
  if (requested && runIds.includes(requested)) return requested;
  if (pinnedRunId && runIds.includes(pinnedRunId)) return pinnedRunId;
  return runIds[0] ?? null;
}

/**
 * `Link copied: 5 runs · baseline #2B · Matrix · Differ` (RC#46): what the
 * link opens, so the person pasting it knows.
 */
export function copyLinkMessage({ runCount, baselineLabel, tab, filter }) {
  return [
    `Link copied: ${fmtCount(runCount, "run")}`,
    baselineLabel ? `baseline ${baselineLabel}` : null,
    tab ? tab[0].toUpperCase() + tab.slice(1) : null,
    filter || null,
  ]
    .filter(Boolean)
    .join(" · ");
}

/**
 * Compare runs page (PRODUCT-SPEC §3.10.2, §2.1):
 * `/dashboard/develop/:dataset/compare/data|matrix|summary?runs=&baseline=&from=`.
 * Header Back (→ `from`) · `Compare runs` · `{n} runs · {dataset}` ·
 * Iterate · Copy link; the R2 config strip and lineage ribbon; the same
 * Data · Matrix · Summary tabs as the experiment page.
 */
const CompareWrapperView = ({ tab }) => {
  const { dataset } = useParams();
  const navigate = useNavigate();
  const { pathname, search } = useLocation();
  const url = useResultsUrlState();
  const { patch } = url;
  const runIds = url.runIds;
  const { data: datasetRuns } = useDatasetRuns(dataset);
  const pinned = datasetRuns?.pinned_run_id ?? null;
  const baselineId = compareBaseline(runIds, url.baseline, pinned, {
    allowNone: tab === "summary",
  });
  // R4 lane X (8a): always read the runs (a link without `runs=` opens the
  // agent's latest version vs its previous one).
  const { data: runConfigs } = useRunConfigs(dataset);
  const allRuns = useMemo(() => runConfigs?.results ?? [], [runConfigs]);
  useEffect(() => {
    if (runIds.length || !allRuns.length) return;
    const d = defaultCompare(allRuns);
    if (d)
      patch({ runs: d.runIds.join(","), baseline: d.baselineId, cand: d.cand });
  }, [runIds.length, allRuns, patch]);
  // R3: with no `cand=`, the page's candidate is the Summary's (the best
  // verdict); same R5 read (and cache entry) as the compare Summary.
  const cand = readR2Params(search).cand;
  const pageCandidate = useCandidateRunId(dataset, {
    runIds,
    baselineId: baselineId === "none" ? null : baselineId,
    analysisBaselineId: baselineId ?? undefined,
    cand,
  });
  // The strip's focused candidate: `cand=` when it is in view, else the
  // drawer's focused run (`run=`), else the page's candidate (R2-SPEC §2.3).
  // 8a: one candidate per page — cand= › latest version › best verdict.
  const theCandidate = pickCandidate({
    runs: allRuns,
    runIds,
    baselineId: baselineId === "none" ? null : baselineId,
    cand,
    fallback: pageCandidate,
  }).id;
  const candidateId = stripCandidate({
    cand: theCandidate,
    focused: new URLSearchParams(search).get("run") ?? theCandidate,
    baselineId,
    runIds,
  });
  // Write the default into the URL once, so every screen reads one `cand=`.
  useEffect(() => {
    if (!cand && theCandidate && allRuns.length) patch({ cand: theCandidate });
  }, [cand, theCandidate, allRuns.length, patch]);
  const tags = useMemo(() => versionTags(allRuns), [allRuns]);
  const colors = useRunColors(
    runIds,
    baselineId === "none" ? null : baselineId,
  );
  const runsInView = useMemo(
    () => allRuns.filter((r) => runIds.includes(r.run_id)),
    [allRuns, runIds],
  );
  // Below 1440 px the Copy link button is its icon (the header keeps one line).
  const wideHeader = useMediaQuery("(min-width:1440px)");
  const { role } = useAuthContext();
  const readOnly = !RolePermission.DATASETS[PERMISSIONS.UPDATE][role];
  const { data: datasets } = useDevelopDatasetList();
  const datasetName = useMemo(
    () => (datasets ?? []).find((d) => d.datasetId === dataset)?.name ?? null,
    [datasets, dataset],
  );

  const setRuns = useCallback(
    (ids) => patch({ runs: ids.slice(0, MAX_COMPARE_RUNS).join(",") || null }),
    [patch],
  );

  const back = () =>
    navigate(
      url.from?.startsWith("/dashboard/")
        ? url.from
        : `/dashboard/develop/${dataset}?tab=experiments`,
    );

  return (
    <Box
      sx={{
        backgroundColor: "background.paper",
        flex: 1,
        display: "flex",
        flexDirection: "column",
        gap: 1,
        minHeight: 0,
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={1.5}
        sx={{ pt: 1.5, px: 1, flexWrap: "wrap", rowGap: 1 }}
      >
        <BackButton onBack={back} />
        <Typography typography="m3" fontWeight="fontWeightSemiBold">
          Compare runs
        </Typography>
        <Typography typography="s1" color="text.secondary" noWrap>
          {fmtCount(runIds.length, "run")}
          {datasetName ? " · " : ""}
          {datasetName ? (
            <Link
              component={RouterLink}
              to={`/dashboard/develop/${dataset}`}
              color="inherit"
              underline="hover"
            >
              {datasetName}
            </Link>
          ) : null}
        </Typography>
        {runIds.length > 1 && baselineId && baselineId !== "none" ? (
          <Stack direction="row" alignItems="center" gap={0.75} sx={{ ml: 1 }}>
            <BaselinePicker
              datasetId={dataset}
              value={baselineId}
              pinnedRunId={pinned}
              runsInView={runsInView}
              restrictTo={runIds}
              onChange={(id) => patch({ baseline: id, cand: null })}
            />
            <CandidateSelect
              runs={runsInView}
              value={candidateId}
              baselineId={baselineId}
              colors={colors}
              tags={tags}
              onChange={(id) => patch({ cand: id })}
            />
          </Stack>
        ) : null}
        <Box sx={{ flex: 1 }} />
        <IterateButton
          datasetId={dataset}
          runIds={runIds}
          baselineId={baselineId === "none" ? null : baselineId}
        />
        <Button
          variant="outlined"
          size="medium"
          aria-label="Share (Copy link)"
          startIcon={
            wideHeader ? <Iconify icon="mdi:link-variant" width={18} /> : null
          }
          sx={wideHeader ? undefined : { minWidth: 0, px: 1 }}
          onClick={async () => {
            // IT-R1 G2 P6: the link drops the personal back-path (`from`)
            // and keeps proto_stage; the toast says what it opens and
            // shows the URL.
            const link = shareLinkOf(window.location.href);
            const ok = await copyText(link);
            const sp = new URLSearchParams(search);
            const base = (runConfigs?.results ?? []).find(
              (r) => r.run_id === baselineId,
            );
            const summary = [
              shareLinkSummary({
                runCount: runIds.length,
                baselineLabel: base?.label,
                tab,
                show: sp.get("show") ?? null,
                sort: sp.get("sort") ?? null,
                search: sp.get("search") ?? null,
              }),
              sp.get("row") ? "the open row" : null,
            ]
              .filter(Boolean)
              .join(" · ");
            notify(
              ok ? (
                <Box sx={{ minWidth: 0, maxWidth: 340 }}>
                  <Box>{`Link copied · it opens ${summary}`}</Box>
                  {/* IT-R2 G2 P12: the full URL with its host (what the
                      clipboard holds), wrapped, never a relative path. */}
                  {/* IT-R5 G6 S3: one line with an ellipsis (the full
                      URL on hover). */}
                  <Box
                    component="span"
                    data-testid="share-link-url"
                    title={link}
                    sx={{
                      display: "block",
                      typography: "s3",
                      color: "text.secondary",
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      maxWidth: "100%",
                    }}
                  >
                    {link}
                  </Box>
                </Box>
              ) : (
                "Couldn't copy the link. Copy it from the address bar."
              ),
              { key: "compare-copy-link", variant: ok ? "success" : "error" },
            );
          }}
        >
          {wideHeader ? (
            "Share"
          ) : (
            <Iconify icon="mdi:link-variant" width={18} />
          )}
        </Button>
      </Stack>
      <Stack
        direction="row"
        alignItems="center"
        data-testid="what-changed-line"
        sx={{ minWidth: 0, flexWrap: "wrap", columnGap: 1, rowGap: 0.5 }}
      >
        <ConfigDiffStrip
          baseline={baselineId === "none" ? null : baselineId}
          candidate={candidateId}
          runs={runConfigs?.results}
          candidates={runIds}
          onCandidate={(id) => patch({ cand: id })}
          onBaseline={(id) => patch({ baseline: id, cand: null })}
          readOnly={readOnly}
        />
        <LineageRibbon
          compact
          datasetId={dataset}
          runIds={runIds}
          baselineId={baselineId === "none" ? null : baselineId}
        />
      </Stack>
      <ExperimentBar
        basePath={`/dashboard/develop/${dataset}/compare`}
        focusRun={
          (runConfigs?.results ?? []).find((r) => r.run_id === candidateId) ??
          null
        }
      />
      {runIds.length === 0 ? (
        <Stack alignItems="center" spacing={1} sx={{ py: 8 }}>
          <Typography typography="m3">Pick runs to compare</Typography>
          <Typography typography="s1" color="text.secondary">
            Select runs on the Experiments tab, then Compare runs.
          </Typography>
          <Button
            variant="contained"
            onClick={() =>
              navigate(`/dashboard/develop/${dataset}?tab=experiments`)
            }
          >
            Go to experiments
          </Button>
        </Stack>
      ) : (
        <>
          <IterateResultBanner
            datasetId={dataset}
            runIds={runIds}
            baselineId={baselineId === "none" ? null : baselineId}
          />
          {tab === "matrix" ? (
            <MatrixView
              mode="compare"
              datasetId={dataset}
              runIds={runIds}
              baselineId={baselineId === "none" ? null : baselineId}
            />
          ) : tab === "paths" ? ( // R4-F frozen line
            <PathsView
              datasetId={dataset}
              runIds={runIds}
              baselineId={baselineId === "none" ? null : baselineId}
            />
          ) : tab === "summary" ? (
            <ExperimentAnalysisView
              datasetId={dataset}
              runIds={runIds}
              baselineId={baselineId ?? undefined}
              mode="compare"
            />
          ) : (
            <RunResultsView
              datasetId={dataset}
              runIds={runIds}
              baselineId={baselineId === "none" ? runIds[0] : baselineId}
              mode="compare"
              onBaselineChange={(id) => patch({ baseline: id })}
              onRemoveRun={(id) => {
                const next = runIds.filter((r) => r !== id);
                patch({
                  runs: next.join(",") || null,
                  baseline:
                    url.baseline === id ? next[0] ?? null : url.baseline,
                });
              }}
              onAddRuns={(ids) => setRuns([...runIds, ...ids])}
            />
          )}
        </>
      )}
      <IterateDock
        datasetId={dataset}
        runIds={runIds}
        baselineId={baselineId === "none" ? null : baselineId}
      />
      <RunActionConfirmHost />
    </Box>
  );
};

CompareWrapperView.propTypes = {
  tab: PropTypes.oneOf(["data", "matrix", "paths", "summary"]).isRequired, // R4-F frozen line
};

export default CompareWrapperView;
