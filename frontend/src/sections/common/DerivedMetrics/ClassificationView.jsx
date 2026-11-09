// Explore › Classification (W5-4 B3, AG-11): per run n, unparsed, accuracy
// with its 95% interval, macro F1 and the positive class; the confusion
// matrix of one run (truth rows × predicted columns, shaded by the share of
// the truth row) whose cells open exactly their rows in the results with a
// rows pill `Truth refund → predicted billing (4)`; per-class precision /
// recall / F1 with support. Computed only in the mock's w5Classification.js.
import React, { useState } from "react";
import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import { alpha, useTheme } from "@mui/material/styles";
import {
  Box,
  Button,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import axios from "src/utils/axios";
import { w5Path } from "src/sections/common/Analysis/workbench/w5Api";
import ClassificationSetupDialog from "./ClassificationSetupDialog";
import {
  CLASSIFICATION_KEY,
  accuracyText,
  cellIndex,
  cellLabel,
  cellPillLabel,
  f2,
  rowShare,
  setupLine,
  topConfusion,
} from "./classificationModel";

const cellSx = { px: 1, py: 0.5, borderBottom: 1, borderColor: "divider" };
const NUM = { fontVariantNumeric: "tabular-nums" };

function Table({ head, rows, testid }) {
  return (
    <Box
      component="table"
      data-testid={testid}
      sx={{ borderCollapse: "collapse", typography: "s2" }}
    >
      <thead>
        <tr>
          {head.map((h) => (
            <Box
              component="th"
              key={h}
              sx={{ ...cellSx, textAlign: "left", color: "text.secondary" }}
            >
              {h}
            </Box>
          ))}
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </Box>
  );
}

Table.propTypes = {
  head: PropTypes.array.isRequired,
  rows: PropTypes.node,
  testid: PropTypes.string,
};

function Matrix({ m, onOpen }) {
  const theme = useTheme();
  const at = cellIndex(m.matrix);
  const labels = m.matrix.labels;
  return (
    <Box sx={{ overflowX: "auto" }}>
      <Box
        component="table"
        data-testid="confusion-matrix"
        sx={{ borderCollapse: "collapse", typography: "s2" }}
      >
        <thead>
          <tr>
            <Box component="th" sx={{ ...cellSx, color: "text.secondary" }}>
              Truth ↓ · predicted →
            </Box>
            {labels.map((l) => (
              <Box component="th" key={l} sx={{ ...cellSx, ...NUM }}>
                {l}
              </Box>
            ))}
          </tr>
        </thead>
        <tbody>
          {labels.map((t) => (
            <tr key={t}>
              <Box component="th" sx={{ ...cellSx, textAlign: "left" }}>
                {t}
              </Box>
              {labels.map((p) => {
                const c = at(t, p);
                const share = rowShare(m.matrix, c);
                const base =
                  t === p
                    ? theme.palette.success.main
                    : theme.palette.error.main;
                return (
                  <Box
                    component="td"
                    key={p}
                    data-cell={`${t}|${p}`}
                    data-count={c?.count ?? 0}
                    sx={{
                      ...cellSx,
                      ...NUM,
                      textAlign: "center",
                      minWidth: 56,
                      bgcolor: c
                        ? alpha(base, 0.08 + 0.5 * share)
                        : "transparent",
                    }}
                  >
                    {c ? (
                      <Button
                        size="small"
                        onClick={() => onOpen(c)}
                        aria-label={cellLabel(c)}
                        sx={{ minWidth: 0, color: "text.primary" }}
                      >
                        {c.count}
                      </Button>
                    ) : (
                      <Typography typography="s3" color="text.disabled">
                        0
                      </Typography>
                    )}
                  </Box>
                );
              })}
            </tr>
          ))}
        </tbody>
      </Box>
    </Box>
  );
}

Matrix.propTypes = { m: PropTypes.object.isRequired, onOpen: PropTypes.func };

export default function ClassificationView({ datasetId, runs, onOpenRows }) {
  const path = w5Path("classification", datasetId);
  const runIds = runs.map((r) => r.run_id);
  const [dialog, setDialog] = useState(false);
  const [pick, setPick] = useState(null);
  const q = useQuery({
    queryKey: [CLASSIFICATION_KEY, datasetId, runIds.join(",")],
    queryFn: () => axios.get(path, { params: { runs: runIds.join(",") } }),
    select: (res) => res?.data?.result,
    enabled: Boolean(path && runIds.length),
  });
  if (!path) return null;
  const d = q.data;
  const labelOf = (id) => runs.find((r) => r.run_id === id)?.label ?? id;
  const parsedRuns = runIds.filter((id) => d?.runs?.[id]?.n > 0);
  const runId =
    pick && d?.runs?.[pick]
      ? pick
      : parsedRuns[parsedRuns.length - 1] ?? runIds[0];
  const m = d?.runs?.[runId];
  const top = m ? topConfusion(m.matrix) : null;
  return (
    <Stack gap={2} sx={{ p: 0.5 }} data-testid="classification">
      <Stack direction="row" alignItems="center" gap={1.5} flexWrap="wrap">
        <Typography typography="s1" fontWeight={600}>
          Classification
        </Typography>
        {d?.setup && (
          <Typography
            typography="s2"
            color="text.secondary"
            data-testid="classification-setup"
          >
            {setupLine(d.setup, d.columns)}
          </Typography>
        )}
        <Button size="small" variant="outlined" onClick={() => setDialog(true)}>
          {d?.setup ? "Edit setup" : "Set up classification"}
        </Button>
      </Stack>
      {d && !d.setup && (
        <Typography typography="s2" color="text.secondary">
          Pick the column that holds the right label and where each run&apos;s
          output names its label; accuracy, F1 and a confusion matrix follow.
        </Typography>
      )}
      {d?.setup && (
        <Table
          testid="classification-runs"
          head={[
            "Run",
            "Rows",
            "Unparsed",
            "Accuracy (95%)",
            "Macro F1",
            ...(d.setup.positive ? [`${d.setup.positive} P / R / F1`] : []),
          ]}
          rows={runIds.map((id) => {
            const r = d.runs[id];
            return (
              <tr
                key={id}
                data-run={labelOf(id)}
                data-n={r.n}
                data-acc={r.accuracy.value ?? ""}
                data-f1={r.macro_f1 ?? ""}
              >
                <Box component="td" sx={cellSx}>
                  <Button
                    size="small"
                    variant={id === runId ? "contained" : "text"}
                    onClick={() => setPick(id)}
                    sx={{ minWidth: 0 }}
                  >
                    {labelOf(id)}
                  </Button>
                </Box>
                <Box component="td" sx={{ ...cellSx, ...NUM }}>
                  {r.n}
                </Box>
                <Box component="td" sx={{ ...cellSx, ...NUM }}>
                  {r.unparsed}
                </Box>
                <Box component="td" sx={{ ...cellSx, ...NUM }}>
                  {accuracyText(r.accuracy)}
                </Box>
                <Box component="td" sx={{ ...cellSx, ...NUM }}>
                  {f2(r.macro_f1)}
                </Box>
                {d.setup.positive && (
                  <Box component="td" sx={{ ...cellSx, ...NUM }}>
                    {r.positive
                      ? `${f2(r.positive.precision)} / ${f2(r.positive.recall)} / ${f2(r.positive.f1)}`
                      : "—"}
                  </Box>
                )}
              </tr>
            );
          })}
        />
      )}
      {d?.setup && m && m.n === 0 && (
        <Typography typography="s2" color="text.secondary">
          {`${labelOf(runId)}: no output names a label at ${d.setup.predicted} (${m.unparsed} unparsed).`}
        </Typography>
      )}
      {d?.setup && m && m.n > 0 && (
        <Stack gap={1}>
          <Stack direction="row" alignItems="center" gap={1.5} flexWrap="wrap">
            <TextField
              select
              size="small"
              label="Matrix of"
              value={runId}
              onChange={(e) => setPick(e.target.value)}
              sx={{ minWidth: 160 }}
            >
              {runIds.map((id) => (
                <MenuItem key={id} value={id}>
                  {labelOf(id)}
                </MenuItem>
              ))}
            </TextField>
            {top && (
              <Typography typography="s2" color="text.secondary">
                {`Most common confusion: ${top.truth} → ${top.predicted} (${top.count}). Click a cell to open its rows.`}
              </Typography>
            )}
          </Stack>
          <Matrix
            m={m}
            onOpen={(c) => onOpenRows(c.row_ids, cellPillLabel(c))}
          />
          <Table
            testid="classification-classes"
            head={["Class", "Precision", "Recall", "F1", "Support"]}
            rows={m.classes.map((c) => (
              <tr key={c.label}>
                <Box component="td" sx={cellSx}>
                  {c.label}
                </Box>
                <Box component="td" sx={{ ...cellSx, ...NUM }}>
                  {f2(c.precision)}
                </Box>
                <Box component="td" sx={{ ...cellSx, ...NUM }}>
                  {f2(c.recall)}
                </Box>
                <Box component="td" sx={{ ...cellSx, ...NUM }}>
                  {f2(c.f1)}
                </Box>
                <Box component="td" sx={{ ...cellSx, ...NUM }}>
                  {c.support}
                </Box>
              </tr>
            ))}
          />
        </Stack>
      )}
      {dialog && d && (
        <ClassificationSetupDialog
          open
          onClose={() => setDialog(false)}
          datasetId={datasetId}
          runIds={runIds}
          columns={d.columns ?? []}
          setup={d.setup}
        />
      )}
    </Stack>
  );
}

ClassificationView.propTypes = {
  datasetId: PropTypes.string.isRequired,
  runs: PropTypes.array.isRequired,
  onOpenRows: PropTypes.func.isRequired,
};
