// R2 (L2) · the lineage ribbon (R2-SPEC §4.3.3): one line under the config
// diff strip, only when the runs in view have iterations:
// `Iterations  #2B ─ classify fix · 6 rows → #2F ─ all 30 rows → #2G` ·
// `Compare chain` · `Trend`.
import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  ButtonBase,
  Link,
  Menu,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import { useLocation, useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { enqueueSnackbar } from "src/components/snackbar";
import { useMatrix, useRunConfigs } from "src/api/develop/r2";
import {
  matrixHref,
  resultsGridHref,
  workbenchHref,
} from "src/sections/common/Analysis/analysisLinks";
import ConfigDiffStrip from "src/sections/common/Runs/ConfigDiffStrip";
import { runLabel } from "src/sections/common/Runs/runLabel";
import { chainCompare, hopLabel, lineageBranch } from "./lineageModel";
import { hopCompare } from "../matrix/verdictScope";
import { comparableRunIds } from "../matrix/comparableRuns";

const HopTip = ({ parent, child, runs, matrix }) => {
  // R4 lane X (8b): runNumbers.compareRuns, the same numbers and scope as
  // the run view headline and the Matrix verdict.
  const delta =
    hopCompare(matrix, parent.run_id, child.run_id, {
      labels: Object.fromEntries(
        (runs ?? []).map((r) => [r.run_id, runLabel(r)]),
      ),
    })?.text ?? null;
  return (
    <Stack spacing={0.75} sx={{ maxWidth: 420 }}>
      <ConfigDiffStrip
        baseline={parent}
        candidate={child}
        runs={runs}
        compact
      />
      {delta ? (
        <Typography typography="s2" fontWeight={500}>
          {delta}
        </Typography>
      ) : null}
      <Typography typography="s3" color="text.secondary">
        {`Click to compare ${runLabel(child)} with ${runLabel(parent)}`}
      </Typography>
    </Stack>
  );
};

HopTip.propTypes = {
  parent: PropTypes.object.isRequired,
  child: PropTypes.object.isRequired,
  runs: PropTypes.array,
  matrix: PropTypes.object,
};

const LineageRibbon = ({ datasetId, runIds, baselineId, compact }) => {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  // R4 lane X (8f): `compact` = one short chip that shares the What changed
  // line (`Iterations #2B → #2F → #2G ▸`) and expands in place.
  const [expanded, setExpanded] = useState(false);
  const [branchPick, setBranchPick] = useState(null);
  const [branchAnchor, setBranchAnchor] = useState(null);
  const configs = useRunConfigs(datasetId, {
    enabled: Boolean(runIds?.length),
  });
  const runs = useMemo(() => configs.data?.results ?? [], [configs.data]);
  const byId = useMemo(() => new Map(runs.map((r) => [r.run_id, r])), [runs]);
  const branch = useMemo(
    () => lineageBranch(runs, [baselineId, ...(runIds ?? [])].filter(Boolean)),
    [runs, runIds, baselineId],
  );
  const allPaths = useMemo(
    () =>
      branch.path.length > 1 ? [branch.path, ...branch.otherBranches] : [],
    [branch],
  );
  const path = (branchPick !== null && allPaths[branchPick]) || branch.path;
  const shown = path.slice(-8);
  // The hop numbers read only the runs of the path that line up row by row
  // (one dataset version, trial plan and evaluators): the results read
  // refuses any other mix, and an evaluation re-run sits on its source
  // run's path in another group. A refused read shows no numbers and no
  // toast; the ribbon, its hops and its links stay.
  const { runIds: readable } = comparableRunIds(shown, {
    baselineId: shown[0],
    runs,
  });
  const matrix = useMatrix(
    {
      datasetId,
      runIds: readable.length > 1 ? readable : [],
      baselineId: readable[0],
    },
    { enabled: readable.length > 1, meta: { errorHandled: true } },
  );
  // A hop's numbers only when both of its runs were read.
  const hopMatrix = (fromId, toId) =>
    readable.includes(fromId) && readable.includes(toId)
      ? matrix.data
      : undefined;

  if (path.length < 2) return null;

  const tab = pathname.endsWith("/matrix") ? "matrix" : "data";
  const pairHref = (a, b) =>
    tab === "matrix"
      ? matrixHref({
          mode: "compare",
          datasetId,
          runIds: [a, b],
          baselineId: a,
        })
      : resultsGridHref({
          mode: "compare",
          datasetId,
          runIds: [a, b],
          baselineId: a,
        });
  const chain = chainCompare(path);
  const openChain = () => {
    if (chain.capped)
      enqueueSnackbar(
        `Showing the last ${chain.runIds.length} of ${chain.total - 1} iterations`,
        { variant: "info" },
      );
    navigate(
      tab === "matrix"
        ? matrixHref({
            mode: "compare",
            datasetId,
            runIds: chain.runIds,
            baselineId: chain.baselineId,
          })
        : resultsGridHref({
            mode: "compare",
            datasetId,
            runIds: chain.runIds,
            baselineId: chain.baselineId,
          }),
    );
  };
  const trendHref = workbenchHref({
    datasetId,
    runIds: chain.runIds,
    baselineId: chain.baselineId,
    chart: "trend",
    cols: "iteration",
  });
  const inView = new Set(runIds ?? []);
  const nodeSx = (theme, active) => ({
    px: 0.75,
    height: 22,
    borderRadius: 0.5,
    typography: "s2",
    fontWeight: 600,
    border: "1px solid",
    borderColor: active ? "primary.main" : "divider",
    color: active ? "primary.main" : "text.primary",
    bgcolor: active
      ? alpha(theme.palette.primary.main, 0.12)
      : "background.paper",
  });
  const others = allPaths.length - 1;
  if (compact && !expanded)
    return (
      <CustomTooltip
        show
        size="small"
        arrow
        title={`${path.length} runs in this chain. Click for each step, Compare chain and Trend.`}
      >
        <ButtonBase
          data-testid="lineage-ribbon-compact"
          onClick={() => setExpanded(true)}
          aria-expanded={false}
          sx={{
            px: 0.75,
            height: 22,
            gap: 0.5,
            borderRadius: 0.5,
            typography: "s2",
            color: "text.secondary",
            flexShrink: 0,
            "&:hover": { bgcolor: "action.hover" },
          }}
        >
          Iterations
          <Box component="span" sx={{ color: "text.primary", fontWeight: 600 }}>
            {shown
              .map((id) => runLabel(byId.get(id) ?? { run_id: id }))
              .join(" → ")}
          </Box>
          <Iconify icon="mdi:chevron-right" width={14} />
        </ButtonBase>
      </CustomTooltip>
    );

  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.75}
      data-testid="lineage-ribbon"
      sx={{ px: 1, minWidth: 0, flexWrap: "wrap", rowGap: 0.5 }}
    >
      {compact ? (
        <ButtonBase
          onClick={() => setExpanded(false)}
          aria-expanded
          sx={{
            typography: "s2",
            color: "text.secondary",
            mr: 0.5,
            borderRadius: 0.5,
            px: 0.25,
          }}
        >
          Iterations
          <Iconify icon="mdi:chevron-down" width={14} />
        </ButtonBase>
      ) : (
        <Typography typography="s2" color="text.secondary" sx={{ mr: 0.5 }}>
          Iterations
        </Typography>
      )}
      {shown.map((id, i) => {
        const run = byId.get(id);
        if (!run) return null;
        const parent = i > 0 ? byId.get(shown[i - 1]) : null;
        const label = runLabel(run);
        const node = parent ? (
          <CustomTooltip
            show
            title={
              <HopTip
                parent={parent}
                child={run}
                runs={runs}
                matrix={hopMatrix(parent.run_id, id)}
              />
            }
            size="small"
            placement="bottom-start"
          >
            <ButtonBase
              aria-label={`Compare ${label} with ${runLabel(parent)}`}
              onClick={() => navigate(pairHref(parent.run_id, id))}
              sx={(theme) => nodeSx(theme, inView.has(id))}
            >
              {label}
            </ButtonBase>
          </CustomTooltip>
        ) : (
          <CustomTooltip
            show
            title={`Root run: ${run.name ?? label}`}
            size="small"
          >
            <Box
              component="span"
              sx={(theme) => ({
                ...nodeSx(theme, inView.has(id)),
                display: "inline-flex",
                alignItems: "center",
              })}
            >
              {label}
            </Box>
          </CustomTooltip>
        );
        const next = shown[i + 1] ? byId.get(shown[i + 1]) : null;
        return (
          <React.Fragment key={id}>
            {node}
            {next ? (
              <CustomTooltip
                show
                title={
                  <HopTip
                    parent={run}
                    child={next}
                    runs={runs}
                    matrix={hopMatrix(id, next.run_id)}
                  />
                }
                size="small"
                placement="bottom-start"
              >
                <ButtonBase
                  data-testid="lineage-hop"
                  onClick={() => navigate(pairHref(id, next.run_id))}
                  sx={{
                    typography: "s2",
                    color: "text.secondary",
                    gap: 0.5,
                    borderRadius: 0.5,
                    px: 0.25,
                    "&:hover": {
                      color: "text.primary",
                      bgcolor: "action.hover",
                    },
                  }}
                >
                  <Box component="span" aria-hidden>
                    ─
                  </Box>
                  {hopLabel(run, next)}
                  <Iconify icon="mdi:arrow-right" width={14} />
                </ButtonBase>
              </CustomTooltip>
            ) : null}
          </React.Fragment>
        );
      })}
      {others > 0 ? (
        <>
          <Button
            size="small"
            color="inherit"
            endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
            onClick={(e) => setBranchAnchor(e.currentTarget)}
            sx={{
              typography: "s2",
              color: "text.secondary",
              minWidth: 0,
              py: 0,
            }}
          >
            {`${others} other ${others === 1 ? "branch" : "branches"}`}
          </Button>
          <Menu
            anchorEl={branchAnchor}
            open={Boolean(branchAnchor)}
            onClose={() => setBranchAnchor(null)}
          >
            {allPaths.map((p, i) => (
              <MenuItem
                key={p.join(",")}
                selected={p === path}
                onClick={() => {
                  setBranchAnchor(null);
                  setBranchPick(i);
                }}
                sx={{ typography: "s2" }}
              >
                {p
                  .map((id) => runLabel(byId.get(id) ?? { run_id: id }))
                  .join(" → ")}
              </MenuItem>
            ))}
          </Menu>
        </>
      ) : null}
      <Typography typography="s2" color="text.disabled">
        ·
      </Typography>
      <CustomTooltip
        show
        title={
          chain.capped
            ? `Showing the last ${chain.runIds.length} of ${chain.total - 1} iterations`
            : `Compare all ${chain.runIds.length} runs, ${runLabel(byId.get(chain.baselineId) ?? {})} as baseline`
        }
        size="small"
        arrow
      >
        <Link
          component="button"
          type="button"
          typography="s2"
          underline="hover"
          onClick={openChain}
        >
          Compare chain
        </Link>
      </CustomTooltip>
      <Typography typography="s2" color="text.disabled">
        ·
      </Typography>
      <CustomTooltip
        show
        title="pass@1 across the iterations (Workbench)"
        size="small"
        arrow
      >
        <Link
          component="button"
          type="button"
          typography="s2"
          underline="hover"
          onClick={() => navigate(trendHref)}
        >
          Trend
        </Link>
      </CustomTooltip>
    </Stack>
  );
};

LineageRibbon.propTypes = {
  compact: PropTypes.bool,
  datasetId: PropTypes.string,
  experimentId: PropTypes.string,
  runIds: PropTypes.arrayOf(PropTypes.string),
  runId: PropTypes.string,
  baselineId: PropTypes.string,
  trial: PropTypes.number,
  rowId: PropTypes.string,
};

export default LineageRibbon;
