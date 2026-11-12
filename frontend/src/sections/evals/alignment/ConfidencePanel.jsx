import React, { useState } from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  MenuItem,
  Select,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import { useConfidence } from "./alignmentApi";
import { binRows, confidenceLabel, openRowsHref } from "./agreementModel";

/**
 * Decision confidence (decision-model judges only): a run's cells binned
 * by their top-answer probability. Click a bin, or drag across bins, then
 * Open rows with a `confidence a–b` pill.
 */
const ConfidencePanel = ({ templateId }) => {
  const theme = useTheme();
  const navigate = useNavigate();
  const [runId, setRunId] = useState(null);
  const [range, setRange] = useState(null);
  const [dragFrom, setDragFrom] = useState(null);
  const { data } = useConfidence(templateId, runId);
  if (!data?.runs?.length) return null;
  const bins = data.bins ?? [];
  const max = Math.max(1, ...bins.map((b) => b.count));
  const ids = range ? binRows(bins, range[0], range[1]) : [];
  const lo = range ? Math.min(...range) : null;
  const hi = range ? Math.max(...range) : null;
  const label = range ? confidenceLabel(bins[lo].x0, bins[hi].x1) : null;
  const run = data.runs.find((r) => r.id === data.run_id);
  const href = run
    ? openRowsHref(
        {
          run_id: run.id,
          dataset_id: run.dataset_id,
          experiment_id: run.experiment_id,
        },
        ids,
        label,
      )
    : null;
  return (
    <Stack gap={1} data-testid="w7-confidence">
      <Stack direction="row" gap={1.5} alignItems="center">
        <Typography typography="s1" fontWeight={600}>
          Decision confidence
        </Typography>
        <Select
          size="small"
          value={data.run_id ?? ""}
          onChange={(e) => {
            setRunId(e.target.value);
            setRange(null);
          }}
          inputProps={{ "aria-label": "Run" }}
          sx={{ typography: "s2", height: 30, minWidth: 120 }}
        >
          {data.runs.map((r) => (
            <MenuItem key={r.id} value={r.id} sx={{ typography: "s2" }}>
              {r.label}
            </MenuItem>
          ))}
        </Select>
        <Typography typography="s3" color="text.secondary">
          {data.n} cells · top-answer probability
        </Typography>
      </Stack>
      <Stack
        direction="row"
        alignItems="flex-end"
        gap={0.5}
        sx={{ height: 120, userSelect: "none" }}
        onMouseLeave={() => setDragFrom(null)}
      >
        {bins.map((b, i) => {
          const on = range && i >= lo && i <= hi;
          return (
            <Tooltip
              key={b.x0}
              title={`${b.count} cells · ${confidenceLabel(b.x0, b.x1)}`}
            >
              <Stack
                data-testid={`w7-bin-${i}`}
                onMouseDown={() => {
                  setDragFrom(i);
                  setRange([i, i]);
                }}
                onMouseEnter={() => dragFrom != null && setRange([dragFrom, i])}
                onMouseUp={() => setDragFrom(null)}
                justifyContent="flex-end"
                alignItems="stretch"
                sx={{ flex: 1, height: "100%", cursor: "pointer" }}
              >
                <Typography
                  typography="s3"
                  color="text.secondary"
                  textAlign="center"
                  sx={{ fontVariantNumeric: "tabular-nums" }}
                >
                  {b.count || ""}
                </Typography>
                <Box
                  sx={{
                    height: `${Math.max(3, (b.count / max) * 80)}%`,
                    bgcolor: alpha(theme.palette.primary.main, on ? 0.8 : 0.3),
                    borderRadius: 0.5,
                  }}
                />
              </Stack>
            </Tooltip>
          );
        })}
      </Stack>
      <Stack direction="row" justifyContent="space-between">
        {[0, 0.5, 1].map((x) => (
          <Typography key={x} typography="s3" color="text.secondary">
            {x.toFixed(1)}
          </Typography>
        ))}
      </Stack>
      {range ? (
        <Stack direction="row" gap={1.5} alignItems="center">
          <Typography typography="s2" data-testid="w7-bin-pick">
            {ids.length} rows · {label}
          </Typography>
          {href ? (
            <Button
              size="small"
              variant="outlined"
              onClick={() => navigate(href)}
              data-testid="w7-bin-open"
            >
              Open rows
            </Button>
          ) : null}
        </Stack>
      ) : null}
    </Stack>
  );
};

ConfidencePanel.propTypes = { templateId: PropTypes.string.isRequired };

export default ConfidencePanel;
