import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  Checkbox,
  ListItemText,
  MenuItem,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import {
  OUTCOME_SCOPES,
  denominatorLine,
  trialLabelOf,
} from "../../_mock/evalEvidence";

/*
  Which trials an evaluation reads — the same for both modes of the Evaluate
  tab. This run's trials (all, failed or passed), a snapshot of the results
  filters, or the rows ticked in the table; other finished runs of the
  environment can be added. Then the exact count, and what each trial that
  can't be read is missing, before anything is spent.
*/
const FieldLabel = ({ children }) => (
  <Typography
    sx={{ typography: "s1", fontWeight: 500, color: "text.primary", mb: 0.75 }}
  >
    {children}
  </Typography>
);
FieldLabel.propTypes = { children: PropTypes.node };

const cellSx = { typography: "s1", color: "text.secondary", py: 0, px: 1.5 };
const headSx = {
  typography: "s2",
  fontWeight: 500,
  color: "text.tertiary",
  bgcolor: "transparent",
  height: TABLE_HEADER_HEIGHT.default,
  py: 0,
  px: 1.5,
};

export default function ScopePicker({
  ctx,
  filter,
  selectedIds,
  state,
  runLabel,
}) {
  const {
    scope,
    setScope,
    runIds,
    setRunIds,
    hasFilter,
    hasSelection,
    countOf,
  } = state;
  const others = ctx.others || [];
  /* A filter or a hand-picked selection belongs to this run alone. */
  const thisRunOnly = scope === "filter" || scope === "selected";

  const options = [
    ...OUTCOME_SCOPES.map((s) => ({
      value: s.value,
      label: s.label,
      n: countOf(s.value),
    })).filter((s) => s.value === "all" || s.n > 0 || s.value === scope),
    ...(hasFilter
      ? [
          {
            value: "filter",
            label: `Current filter · ${filter.name}`,
            n: countOf("filter"),
          },
        ]
      : []),
    ...(hasSelection
      ? [
          {
            value: "selected",
            label: `Selected trials (${selectedIds.length})`,
            n: countOf("selected"),
          },
        ]
      : []),
  ];

  return (
    <>
      <Box>
        <FieldLabel>Which trials</FieldLabel>
        <TextField
          select
          size="small"
          value={scope}
          onChange={(e) => setScope(e.target.value)}
          sx={{ minWidth: 320, maxWidth: "100%" }}
        >
          {options.map((s) => (
            <MenuItem key={s.value} value={s.value}>
              {s.label} · {s.n}
            </MenuItem>
          ))}
        </TextField>
        <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}>
          {scope === "filter"
            ? `${runLabel}'s filters as they are now`
            : scope === "selected"
              ? `Ticked in ${runLabel}'s results`
              : `From ${runLabel}${runIds.length ? ` + ${runIds.length} more` : ""}`}
        </Typography>
      </Box>

      {others.length > 0 && (
        <Box>
          <FieldLabel>Runs</FieldLabel>
          <TextField
            select
            size="small"
            disabled={thisRunOnly}
            value={runIds}
            onChange={(e) => setRunIds(e.target.value)}
            SelectProps={{
              multiple: true,
              displayEmpty: true,
              renderValue: (v) =>
                v.length
                  ? `${runLabel} and ${v.length} other ${v.length === 1 ? "run" : "runs"}`
                  : `${runLabel} only`,
            }}
            sx={{ minWidth: 320, maxWidth: "100%" }}
          >
            {others.map((r) => (
              <MenuItem key={r.id} value={r.id}>
                <Checkbox
                  size="small"
                  checked={runIds.includes(r.id)}
                  sx={{ p: 0.5, mr: 0.5 }}
                />
                <ListItemText
                  primary={r.label}
                  secondary={r.meta}
                  primaryTypographyProps={{ typography: "s1" }}
                  secondaryTypographyProps={{ typography: "s3" }}
                />
              </MenuItem>
            ))}
          </TextField>
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}
          >
            {thisRunOnly
              ? "Only this run, with a filter or selection"
              : "Finished runs of this environment"}
          </Typography>
        </Box>
      )}
    </>
  );
}
ScopePicker.propTypes = {
  ctx: PropTypes.object.isRequired,
  filter: PropTypes.object,
  selectedIds: PropTypes.array,
  state: PropTypes.object.isRequired,
  runLabel: PropTypes.string.isRequired,
};

/* The exact selection: the count, and each trial that can't be read. */
export function ScopeReview({ ctx, state }) {
  const [showMissing, setShowMissing] = useState(false);
  const { trials, missing, readable } = state;
  return (
    <Box>
      <FieldLabel>Trials to evaluate</FieldLabel>
      <Typography sx={{ typography: "s1", color: "text.primary" }}>
        {denominatorLine({
          evaluated: readable,
          requested: trials.length,
          skipped: 0,
          errors: 0,
        })}
      </Typography>
      {missing.length > 0 && (
        <Box sx={{ mt: 0.75 }}>
          <Typography sx={{ typography: "s1", color: "text.secondary" }}>
            {`${missing.length} ${missing.length === 1 ? "trial" : "trials"} can't be read — counted as skipped`}
          </Typography>
          <Button
            size="small"
            variant="outlined"
            onClick={() => setShowMissing((v) => !v)}
            endIcon={
              <Iconify
                icon={
                  showMissing
                    ? "solar:alt-arrow-up-linear"
                    : "solar:alt-arrow-down-linear"
                }
                width={14}
              />
            }
            sx={{ mt: 1, color: "text.primary" }}
          >
            {showMissing ? "Hide missing inputs" : "Show missing inputs"}
          </Button>
          {showMissing && (
            <Table size="small" sx={{ mt: 1, maxWidth: 560 }}>
              <TableHead>
                <TableRow>
                  <TableCell sx={{ ...headSx, width: 140 }}>Trial</TableCell>
                  <TableCell sx={headSx}>Missing</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {missing.map((m) => (
                  <TableRow key={m.key} sx={{ height: ROW_HEIGHT.default }}>
                    <TableCell
                      sx={{
                        ...cellSx,
                        color: "text.primary",
                        fontWeight: 500,
                      }}
                    >
                      {trialLabelOf(m.trial, ctx.runId)}
                    </TableCell>
                    <TableCell sx={cellSx}>
                      {m.lacks.map((l) => l.text).join(", ")}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Box>
      )}
    </Box>
  );
}
ScopeReview.propTypes = {
  ctx: PropTypes.object.isRequired,
  state: PropTypes.object.isRequired,
};
