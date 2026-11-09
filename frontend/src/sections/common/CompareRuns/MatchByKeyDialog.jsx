// Compare a run with a run on another dataset (W5-6 B7, CP-04, E21): pick
// the other dataset and run, match rows by a column both datasets have, the
// input hash or the row id (same dataset only); the summary counts matched /
// only-in / duplicate keys, a paired table per metric gives A, B and Δ with
// its 95% interval, and the rows with the largest drop link to both runs'
// result drawers. Computed only in the mock's w5Match.js.
import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Link,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import axios from "src/utils/axios";
import { resultsGridHref } from "src/sections/common/Analysis/analysisLinks";
import {
  apiErrorText,
  w5Path,
} from "src/sections/common/Analysis/workbench/w5Api";
import {
  fmtMatchDelta,
  fmtMatchValue,
  matchByLabel,
  runName,
  sideLabels,
  summaryLine,
  topDrops,
  verdictOf,
} from "./matchModel";

const cell = { px: 1, py: 0.5, borderBottom: 1, borderColor: "divider" };
const NUM = { fontVariantNumeric: "tabular-nums" };
const TONE = { better: "green.600", worse: "red.600" };

const drawerHref = (side, row) =>
  resultsGridHref({
    datasetId: side.dataset_id,
    runIds: [side.run_id],
    baselineId: side.run_id,
    row: row.example_id,
  });

export default function MatchByKeyDialog({
  open,
  onClose,
  runs,
  defaultRunId,
  defaultOtherRunId,
}) {
  const path = w5Path("resultsMatch");
  const [runA, setRunA] = useState(defaultRunId ?? runs[0]?.run_id ?? "");
  const [dsB, setDsB] = useState("");
  const [runB, setRunB] = useState("");
  const [by, setBy] = useState("");
  const [metricKey, setMetricKey] = useState("");
  const targetsQ = useQuery({
    queryKey: ["w5-match", "targets", runA, runB],
    queryFn: () =>
      axios.get(path, {
        params: { runs: [runA, runB].filter(Boolean).join(",") },
      }),
    select: (res) => res?.data?.result,
    enabled: Boolean(open && path && runA),
  });
  const targets = useMemo(() => targetsQ.data?.targets ?? [], [targetsQ.data]);
  const options = targetsQ.data?.options;
  // A door that names the other run (By dataset's cell) picks its dataset.
  useEffect(() => {
    if (!defaultOtherRunId || dsB || !targets.length) return;
    const home = targets.find((d) =>
      d.runs.some((r) => r.run_id === defaultOtherRunId),
    );
    if (home && defaultOtherRunId !== runA) {
      setDsB(home.id);
      setRunB(defaultOtherRunId);
    }
  }, [defaultOtherRunId, dsB, targets, runA]);
  const byChoices = useMemo(
    () =>
      options
        ? [
            ...options.columns.map((c) => ({ value: c, label: `Column ${c}` })),
            { value: "input_hash", label: "Input hash" },
            {
              value: "row_id",
              label: "Row id (same dataset only)",
              disabled: !options.row_id,
            },
          ]
        : [],
    [options],
  );
  useEffect(() => {
    if (
      byChoices.length &&
      !byChoices.some((c) => c.value === by && !c.disabled)
    )
      setBy(byChoices[0].value);
  }, [byChoices, by]);
  const matchQ = useQuery({
    queryKey: ["w5-match", runA, runB, by],
    queryFn: () =>
      axios.get(path, { params: { runs: `${runA},${runB}`, match_by: by } }),
    select: (res) => res?.data?.result,
    enabled: Boolean(open && path && runA && runB && by && options),
    retry: false,
  });
  const m = matchQ.data;
  const metric =
    m?.metrics?.find((x) => x.key === metricKey) ??
    m?.metrics?.find((x) => x.key.startsWith("eval:")) ??
    m?.metrics?.[0];
  // Every matched row that got worse, worst first (the list scrolls).
  const drops = m && metric ? topDrops(m.rows, metric, m.rows.length) : [];
  const dataset = targets.find((d) => d.id === dsB);
  const names = m ? sideLabels(m) : null;
  if (!path) return null;
  return (
    <Dialog open={open} onClose={onClose} maxWidth="lg" fullWidth>
      <DialogTitle>Compare rows by key</DialogTitle>
      <DialogContent>
        <Stack gap={2} sx={{ pt: 1 }} data-testid="match-dialog">
          <Stack direction="row" gap={1.5} flexWrap="wrap">
            <TextField
              select
              size="small"
              label="Run"
              value={runA}
              onChange={(e) => setRunA(e.target.value)}
              inputProps={{ "aria-label": "Run" }}
              sx={{ minWidth: 220 }}
            >
              {runs.map((r) => (
                <MenuItem key={r.run_id} value={r.run_id}>
                  {r.label} {r.name ? `· ${r.name}` : ""}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              select
              size="small"
              label="Other dataset"
              value={dsB}
              onChange={(e) => {
                setDsB(e.target.value);
                setRunB("");
              }}
              inputProps={{ "aria-label": "Other dataset" }}
              sx={{ minWidth: 260 }}
            >
              {targets.map((d) => (
                <MenuItem key={d.id} value={d.id} disabled={!d.runs.length}>
                  {d.name}
                  {d.runs.length ? "" : " (no runs)"}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              select
              size="small"
              label="Its run"
              value={runB}
              onChange={(e) => setRunB(e.target.value)}
              disabled={!dataset}
              inputProps={{ "aria-label": "Its run" }}
              sx={{ minWidth: 220 }}
            >
              {(dataset?.runs ?? [])
                .filter((r) => r.run_id !== runA)
                .map((r) => (
                  <MenuItem key={r.run_id} value={r.run_id}>
                    {r.label} {r.name ? `· ${r.name}` : ""}
                  </MenuItem>
                ))}
            </TextField>
            <TextField
              select
              size="small"
              label="Match rows by"
              value={byChoices.some((c) => c.value === by) ? by : ""}
              onChange={(e) => setBy(e.target.value)}
              disabled={!byChoices.length}
              inputProps={{ "aria-label": "Match rows by" }}
              sx={{ minWidth: 240 }}
            >
              {byChoices.map((c) => (
                <MenuItem key={c.value} value={c.value} disabled={c.disabled}>
                  {c.label}
                </MenuItem>
              ))}
            </TextField>
          </Stack>
          {matchQ.isError && (
            <Typography typography="s2" color="error.main">
              {apiErrorText(matchQ.error)}
            </Typography>
          )}
          {m && (
            <Stack gap={1.5}>
              <Typography typography="s2" color="text.secondary">
                {`${runName(m.a)} vs ${runName(m.b)} · ${matchByLabel(m.match_by)}`}
              </Typography>
              <Typography
                typography="s1"
                fontWeight={600}
                data-testid="match-summary"
              >
                {summaryLine(m)}
              </Typography>
              <Box
                component="table"
                data-testid="match-metrics"
                sx={{ borderCollapse: "collapse", typography: "s2" }}
              >
                <thead>
                  <tr>
                    {[
                      "Metric",
                      names.a,
                      names.b,
                      "Δ (95%)",
                      "Improved",
                      "Regressed",
                      "",
                    ].map((h, i) => (
                      <Box
                        component="th"
                        key={`${h}-${i}`}
                        sx={{
                          ...cell,
                          textAlign: "left",
                          color: "text.secondary",
                        }}
                      >
                        {h}
                      </Box>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {m.metrics.map((x) => {
                    const v = verdictOf(x);
                    return (
                      <tr
                        key={x.key}
                        data-metric={x.key}
                        data-delta={x.delta ?? ""}
                        data-n={x.n}
                      >
                        <Box component="td" sx={cell}>
                          {x.name}
                        </Box>
                        <Box component="td" sx={{ ...cell, ...NUM }}>
                          {fmtMatchValue(x, x.a)}
                        </Box>
                        <Box component="td" sx={{ ...cell, ...NUM }}>
                          {fmtMatchValue(x, x.b)}
                        </Box>
                        <Box
                          component="td"
                          sx={{
                            ...cell,
                            ...NUM,
                            color: TONE[v] ?? "text.secondary",
                          }}
                        >
                          {fmtMatchDelta(x, x.delta, x.ci)}
                        </Box>
                        <Box component="td" sx={{ ...cell, ...NUM }}>
                          {x.improved}
                        </Box>
                        <Box component="td" sx={{ ...cell, ...NUM }}>
                          {x.regressed}
                        </Box>
                        <Box
                          component="td"
                          sx={{ ...cell, color: TONE[v] ?? "text.secondary" }}
                        >
                          {v}
                        </Box>
                      </tr>
                    );
                  })}
                </tbody>
              </Box>
              {metric && (
                <Stack gap={1}>
                  <Stack direction="row" gap={1.5} alignItems="center">
                    <Typography typography="s1" fontWeight={600}>
                      Largest drops
                    </Typography>
                    <TextField
                      select
                      size="small"
                      value={metric.key}
                      onChange={(e) => setMetricKey(e.target.value)}
                      inputProps={{ "aria-label": "Drops on" }}
                      sx={{ minWidth: 180 }}
                    >
                      {m.metrics.map((x) => (
                        <MenuItem key={x.key} value={x.key}>
                          {x.name}
                        </MenuItem>
                      ))}
                    </TextField>
                    <Typography typography="s2" color="text.secondary">
                      {drops.length
                        ? `${drops.length} matched rows where ${names.b} is worse on ${metric.name}`
                        : `No matched row is worse on ${metric.name}.`}
                    </Typography>
                  </Stack>
                  <Stack gap={1} sx={{ maxHeight: 280, overflowY: "auto" }}>
                    {drops.map((r) => (
                      <Stack
                        key={r.key}
                        direction="row"
                        gap={2}
                        alignItems="center"
                        data-testid="match-drop"
                        sx={{ typography: "s2" }}
                      >
                        <Typography
                          typography="s2"
                          sx={{ ...NUM, minWidth: 110 }}
                        >
                          {fmtMatchDelta(metric, r.delta[metric.key])}
                        </Typography>
                        <Link component={RouterLink} to={drawerHref(m.a, r.a)}>
                          {`${names.a} row ${r.a.row_order}`}
                        </Link>
                        <Link component={RouterLink} to={drawerHref(m.b, r.b)}>
                          {`${names.b} row ${r.b.row_order}`}
                        </Link>
                        <Typography
                          typography="s3"
                          color="text.secondary"
                          noWrap
                          sx={{ maxWidth: 420 }}
                        >
                          {`key ${r.key}`}
                        </Typography>
                      </Stack>
                    ))}
                  </Stack>
                </Stack>
              )}
            </Stack>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
}

MatchByKeyDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  runs: PropTypes.array.isRequired,
  defaultRunId: PropTypes.string,
  defaultOtherRunId: PropTypes.string,
};
