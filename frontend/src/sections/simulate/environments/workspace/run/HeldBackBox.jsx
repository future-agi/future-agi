import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  Collapse,
  Stack,
  Typography,
  useMediaQuery,
} from "@mui/material";

import { useReadinessCases } from "src/api/simulate-environments/readiness";
import { RUN_DIALOG_COPY as COPY } from "../workspace.constants";

const boxSx = {
  border: "1px solid",
  borderColor: "divider",
  borderRadius: 1,
  p: 1.5,
};

// The scenarios a run holds back, from the readiness report: each reason in plain words with how many it holds,
// and, one click away, each held scenario with what holds it. Held ones are never sent.
export default function HeldBackBox({
  envId,
  picks,
  keys,
  report,
  shownRows = [],
  selection,
}) {
  const reduceMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const [showNames, setShowNames] = useState(false);
  const held = selection?.held_back;
  const cases = useReadinessCases(envId, {
    picks,
    keys,
    report,
    enabled: showNames && Boolean(held?.count),
  });
  if (!held?.count) return null;
  // A reason Ready to run already shows as a line isn't said twice.
  const reasons = Object.entries(held.by ?? {}).filter(
    ([reason, n]) => n > 0 && !shownRows.includes(reason),
  );
  const rowName = Object.fromEntries(
    (selection.held_by ?? []).map((one) => [one.id, one.name]),
  );
  // Each held scenario with its reasons once the cases page lands; the names alone until then.
  const items = cases.data
    ? cases.data
        .filter((one) => one.by?.length)
        .map((one) => ({
          key: one.key,
          text: `${one.name || one.key} · ${one.by.map((id) => rowName[id] || id).join(", ")}`,
        }))
    : held.keys.map((key) => ({ key, text: held.names?.[key] || key }));
  const more = held.truncated || held.count > items.length;
  return (
    <Box sx={boxSx} data-testid="run-held-back">
      <Typography
        sx={{ typography: "s1", fontWeight: "fontWeightMedium", mb: 0.5 }}
      >
        {COPY.heldBackTitle(held.count)}
      </Typography>
      <Stack spacing={0.25}>
        {reasons.map(([reason, n]) => (
          <Typography
            key={reason}
            sx={{ typography: "s1", color: "text.secondary" }}
          >
            {COPY.heldReason(selection.reasons?.[reason] || reason, n)}
          </Typography>
        ))}
      </Stack>
      {items.length > 0 && (
        <Button
          size="small"
          color="inherit"
          sx={{ mt: 0.5 }}
          onClick={() => setShowNames((open) => !open)}
        >
          {showNames ? COPY.hideWhich : COPY.showWhich}
        </Button>
      )}
      <Collapse
        in={showNames}
        timeout={reduceMotion ? 0 : undefined}
        unmountOnExit
      >
        <Box
          component="ul"
          sx={{ m: 0, pl: 2, typography: "s3", color: "text.secondary" }}
        >
          {items.map((one) => (
            <li key={one.key}>{one.text}</li>
          ))}
          {more && <li>…</li>}
        </Box>
      </Collapse>
    </Box>
  );
}

HeldBackBox.propTypes = {
  envId: PropTypes.string,
  picks: PropTypes.object,
  // The scenarios the run would send; null is the whole suite.
  keys: PropTypes.arrayOf(PropTypes.string),
  // When the report was worked out, so the names match its counts.
  report: PropTypes.string,
  // The readiness rows Ready to run shows as lines.
  shownRows: PropTypes.arrayOf(PropTypes.string),
  // The report's selection for the picked versions and scenarios.
  selection: PropTypes.shape({
    held_back: PropTypes.shape({
      count: PropTypes.number,
      keys: PropTypes.arrayOf(PropTypes.string),
      names: PropTypes.object,
      by: PropTypes.object,
      truncated: PropTypes.bool,
    }),
    reasons: PropTypes.object,
    held_by: PropTypes.array,
  }),
};
