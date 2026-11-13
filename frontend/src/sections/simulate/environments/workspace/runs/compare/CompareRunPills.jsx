import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  IconButton,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";

import Iconify from "src/components/iconify";
import { COMPARE_COPY } from "../../workspace.constants";
import { compareRunColorValue } from "./compareCells";

// One pill per compared run, bordered in its colour: its number, its agent and
// environment versions (the environment muted) and its whole-run pooled pass rate, as on
// the runs list. The first is the baseline. A run can be removed while more than two remain, and finished
// runs not yet compared can be added until the comparison is full.
export default function CompareRunPills({
  runs,
  candidates = [],
  canRemove,
  full,
  onRemove,
  onAdd,
}) {
  const [anchor, setAnchor] = useState(null);
  return (
    <Stack direction="row" alignItems="center" flexWrap="wrap" gap={1}>
      {runs.map((run) => (
        <Stack
          key={run.id}
          direction="row"
          alignItems="center"
          spacing={0.75}
          data-testid="compare-run-pill"
          sx={{
            pl: 1,
            pr: canRemove ? 0.25 : 1,
            py: 0.5,
            borderRadius: "6px",
            border: "1px solid",
            borderColor: (t) => alpha(compareRunColorValue(t, run), 0.35),
            bgcolor: (t) =>
              alpha(
                compareRunColorValue(t, run),
                t.palette.mode === "dark" ? 0.12 : 0.06,
              ),
          }}
        >
          <Typography
            noWrap
            sx={{ typography: "s3", fontWeight: 600, maxWidth: 280 }}
          >
            {run.ordinal != null && `${COMPARE_COPY.run(run.ordinal)} · `}
            {run.agentLabel && COMPARE_COPY.runLabel(run.agentLabel)}
            <Box
              component="span"
              sx={{ color: "text.tertiary", fontWeight: 500 }}
            >
              {run.agentLabel
                ? run.environmentLabel &&
                  ` × ${COMPARE_COPY.runLabel(null, run.environmentLabel)}`
                : COMPARE_COPY.runLabel(null, run.environmentLabel)}
            </Box>
            {` · ${run.passRate == null ? "-" : `${run.passRate}%`}`}
            {run.measured ? ` ${run.passed}/${run.measured}` : ""}
          </Typography>
          {run.isBaseline && (
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {COMPARE_COPY.baseline}
            </Typography>
          )}
          {canRemove && (
            <Tooltip arrow title={COMPARE_COPY.removeFromComparison}>
              <IconButton
                size="small"
                aria-label={COMPARE_COPY.removeRun(run.ordinal)}
                onClick={() => onRemove(run.id)}
              >
                <Iconify
                  icon="solar:close-linear"
                  width={12}
                  sx={{ color: "text.tertiary" }}
                />
              </IconButton>
            </Tooltip>
          )}
        </Stack>
      ))}
      <Button
        size="small"
        variant="outlined"
        disabled={full}
        onClick={(e) => setAnchor(e.currentTarget)}
        startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
      >
        {COMPARE_COPY.addRun}
      </Button>
      <Menu anchorEl={anchor} open={!!anchor} onClose={() => setAnchor(null)}>
        {candidates.length === 0 && (
          <MenuItem disabled>
            <ListItemText
              primary={COMPARE_COPY.noMoreRuns}
              primaryTypographyProps={{ typography: "s2" }}
            />
          </MenuItem>
        )}
        {candidates.map((run) => (
          <MenuItem
            key={run.executionId}
            onClick={() => {
              onAdd(run.executionId);
              setAnchor(null);
            }}
            sx={{ gap: 1 }}
          >
            <Box sx={{ minWidth: 0 }}>
              <Typography sx={{ typography: "s2" }}>
                {run.label}
                {run.agentVersion ? ` · agent ${run.agentVersion}` : ""}
              </Typography>
            </Box>
            <Typography
              sx={{ typography: "s2", color: "text.secondary", ml: "auto" }}
            >
              {run.passRate == null ? "-" : `${Math.round(run.passRate)}%`}
            </Typography>
          </MenuItem>
        ))}
      </Menu>
    </Stack>
  );
}
CompareRunPills.propTypes = {
  runs: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string,
      ordinal: PropTypes.number,
      colorIndex: PropTypes.number,
      isBaseline: PropTypes.bool,
      agentLabel: PropTypes.string,
      environmentLabel: PropTypes.string,
      passRate: PropTypes.number,
      passed: PropTypes.number,
      measured: PropTypes.number,
    }),
  ).isRequired,
  candidates: PropTypes.array,
  canRemove: PropTypes.bool.isRequired,
  full: PropTypes.bool.isRequired,
  onRemove: PropTypes.func.isRequired,
  onAdd: PropTypes.func.isRequired,
};
