import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Chip,
  Skeleton,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useDatasetRuns } from "src/api/develop/runs";
import { useRunAnalysis } from "src/api/develop/analysis";
import { useRunColors } from "src/sections/common/Runs/runColors";
import {
  EMPTY,
  fmtCount,
  fmtPct,
  NUM_SX,
} from "src/sections/common/format/fmt";
import { VOCAB } from "src/sections/common/vocab";
import { shortRunName } from "src/sections/experiment-detail/ExperimentSummary/Analysis/verdictItems";
import { evalRateRows, latestExperiment } from "./experimentEvals";

/** `#2` from the experiment's run labels (`#2A`). */
const experimentTag = (runs) =>
  String(runs?.[0]?.label ?? "").match(/^#\d+/)?.[0] ?? null;

// One view: every eval shows up to this many; longer lists fold the rest
// behind `Show all N evals` (data-driven, never a mode).
const FOLD_FROM = 8;
const FOLDED_ROWS = 5;

/**
 * Dataset Summary · Evals when the dataset has no evals of its own but its
 * latest experiment scored its runs (R3 analysis fix): the verdict of that
 * experiment and a pass rate per eval per run, so the Evals tab never says
 * `No evaluations added` next to an Experiments tab full of scores. Renders
 * `fallback` (the add-evals empty state) when no experiment has scores.
 */
export default function ExperimentEvalsCard({
  datasetId,
  fallback,
  onAddEvals,
}) {
  const [all, setAll] = useState(false);
  // IT-R5 G2 P4: the borrowed table starts folded (the verdict card above
  // already answers).
  const [open, setOpen] = useState(false);
  const runsQ = useDatasetRuns(datasetId, { include: "metrics" });
  const exp = latestExperiment(
    runsQ.data?.results ?? [],
    runsQ.data?.pinned_run_id ?? null,
  );
  const analysisQ = useRunAnalysis(datasetId, {
    runIds: exp?.runIds ?? [],
    baselineId: exp?.runIds.length > 1 ? exp.baselineId : undefined,
    enabled: Boolean(exp),
  });
  const analysis = analysisQ.data;
  const runs = analysis?.runs ?? [];
  const colors = useRunColors(
    runs.map((r) => r.run_id),
    analysis?.baseline_run_id ?? null,
  );

  if (runsQ.isLoading || (exp && analysisQ.isLoading))
    return <Skeleton variant="rounded" height={260} />;
  if (!exp || !analysis) return fallback;
  const primaryKey = analysis.metrics.find(
    (m) => m.family === "eval" && /success/i.test(m.name ?? ""),
  )?.key;
  const rows = evalRateRows(analysis, { primaryKey });
  if (!rows.length) return fallback;

  const folds = rows.length > FOLD_FROM;
  const limit = all || !folds ? rows.length : FOLDED_ROWS;

  const tag = experimentTag(runs);
  return (
    <Stack gap={1.5} data-testid="dataset-no-evals">
      {/* IT-R5 G2 P4: the empty state names what to do, one primary. */}
      <Stack
        direction="row"
        alignItems="center"
        gap={1.5}
        flexWrap="wrap"
        sx={{
          border: 1,
          borderColor: "divider",
          borderRadius: 1,
          px: 1.5,
          py: 1.25,
        }}
      >
        <Typography typography="s1" sx={{ flex: 1, minWidth: 0 }}>
          {/* L2 G2 R4 (consistency #5): never `No evals` next to an
              experiment that scored its runs with some. */}
          {`No dataset-level evals yet; ${tag ?? "the latest experiment"} uses ${fmtCount(rows.length, "eval")}. Add one to the dataset and every new experiment is scored.`}
        </Typography>
        {onAddEvals ? (
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={onAddEvals}
            startIcon={<Iconify icon="mdi:plus" width={16} />}
            data-testid="add-eval-to-dataset"
          >
            Add eval to dataset
          </Button>
        ) : null}
      </Stack>
      <Box
        data-testid="borrowed-evals"
        sx={{
          border: 1,
          borderColor: "divider",
          borderRadius: 1,
          bgcolor: "background.paper",
          minWidth: 0,
        }}
      >
        <Stack
          direction="row"
          alignItems="center"
          gap={0.75}
          sx={{ px: 1, py: 0.75 }}
        >
          <Button
            size="small"
            color="inherit"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            data-testid="borrowed-evals-toggle"
            startIcon={
              <Iconify
                icon={open ? "mdi:chevron-down" : "mdi:chevron-right"}
                width={16}
              />
            }
            sx={{ typography: "s1", fontWeight: 600, px: 0.5 }}
          >
            {`Evals from ${exp.experimentName ?? "the latest experiment"}`}
          </Button>
          {tag ? (
            <Chip
              size="small"
              label={`From ${tag}`}
              variant="outlined"
              data-testid="borrowed-evals-badge"
              sx={{ height: 20, typography: "s3", color: "text.secondary" }}
            />
          ) : null}
        </Stack>
        {open ? (
          <Typography
            typography="s2"
            color="text.secondary"
            sx={{ px: 1.5, pb: 1 }}
          >
            {`The ${fmtCount(rows.length, "eval")} its latest experiment scored, as the share of ${VOCAB.TRIALS} that passed.`}
          </Typography>
        ) : null}
        {open ? (
          <Stack gap={1.5}>
            <Box sx={{ overflowX: "auto" }} data-qa-clip>
              <Table
                size="small"
                sx={{
                  "& td, & th": {
                    typography: "s2",
                    borderColor: "divider",
                    py: 0.75,
                    whiteSpace: "nowrap",
                  },
                  "& th": {
                    fontWeight: 600,
                    color: "text.secondary",
                    bgcolor: "background.neutral",
                  },
                }}
              >
                <TableHead>
                  <TableRow>
                    <TableCell sx={{ pl: 1.5 }}>Eval</TableCell>
                    {runs.map((r) => (
                      <TableCell key={r.run_id} align="right">
                        <CustomTooltip
                          show
                          size="small"
                          placement="top"
                          title={`${r.label} ${shortRunName(r.name)}${r.run_id === analysis.baseline_run_id ? " · baseline" : ""}`}
                        >
                          <Stack
                            component="span"
                            direction="row"
                            gap={0.5}
                            alignItems="center"
                            justifyContent="flex-end"
                          >
                            <Box
                              component="span"
                              sx={{
                                width: 3,
                                height: 12,
                                borderRadius: 1,
                                bgcolor: colors?.[r.run_id]?.solid,
                              }}
                            />
                            {r.label}
                            {r.run_id === analysis.baseline_run_id ? (
                              <Typography
                                component="span"
                                typography="s3"
                                color="text.disabled"
                              >
                                baseline
                              </Typography>
                            ) : null}
                          </Stack>
                        </CustomTooltip>
                      </TableCell>
                    ))}
                  </TableRow>
                </TableHead>
                <TableBody>
                  {rows.slice(0, limit).map((row) => (
                    <TableRow key={row.key} hover>
                      <TableCell sx={{ pl: 1.5 }}>
                        <Stack direction="row" gap={0.75} alignItems="center">
                          {row.name}
                          {row.kind !== "eval" ? (
                            <Chip
                              size="small"
                              variant="outlined"
                              label={
                                row.kind === "check"
                                  ? VOCAB.checkChip
                                  : VOCAB.judgeChip
                              }
                              sx={{
                                height: 18,
                                borderRadius: 0.5,
                                typography: "s3",
                              }}
                            />
                          ) : null}
                        </Stack>
                      </TableCell>
                      {runs.map((r) => {
                        const v = row.rates[r.run_id];
                        return (
                          <TableCell
                            key={r.run_id}
                            align="right"
                            sx={{
                              ...NUM_SX,
                              fontWeight:
                                row.bestRunId === r.run_id ? 600 : 400,
                              color:
                                v == null ? "text.disabled" : "text.primary",
                            }}
                          >
                            {v == null ? EMPTY : fmtPct(v)}
                          </TableCell>
                        );
                      })}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Box>
            {folds ? (
              <Box sx={{ px: 1.5, pb: 1 }}>
                <Button size="small" onClick={() => setAll((v) => !v)}>
                  {all ? "Show fewer" : `Show all ${rows.length} evals`}
                </Button>
              </Box>
            ) : null}
          </Stack>
        ) : null}
      </Box>
    </Stack>
  );
}

ExperimentEvalsCard.propTypes = {
  datasetId: PropTypes.string,
  fallback: PropTypes.node,
  onAddEvals: PropTypes.func,
};
