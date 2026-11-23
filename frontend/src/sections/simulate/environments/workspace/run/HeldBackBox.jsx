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

import { RUN_DIALOG_COPY as COPY } from "../workspace.constants";

const boxSx = {
  border: "1px solid",
  borderColor: "divider",
  borderRadius: 1,
  p: 1.5,
};

// The scenarios a run holds back, from the readiness selection: each reason in plain words with how many it
// holds, the held scenarios by name, and the readiness lines behind them one click away. Held ones are never sent.
// The selection counts per reason but doesn't say which scenario has which reason, so names are listed together.
export default function HeldBackBox({ selection }) {
  const reduceMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const [showNames, setShowNames] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const held = selection?.held_back;
  if (!held?.count) return null;
  const reasons = Object.entries(held.by ?? {}).filter(([, n]) => n > 0);
  const names = held.keys.map((key) => held.names?.[key] || key);
  const lines = (selection.held_by ?? []).filter((one) => one.text);
  const timeout = reduceMotion ? 0 : undefined;
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
      <Stack direction="row" spacing={1} sx={{ mt: 0.5 }}>
        {names.length > 0 && (
          <Button
            size="small"
            color="inherit"
            onClick={() => setShowNames((open) => !open)}
          >
            {showNames ? COPY.hideWhich : COPY.showWhich}
          </Button>
        )}
        {lines.length > 0 && (
          <Button
            size="small"
            color="inherit"
            onClick={() => setShowDetails((open) => !open)}
          >
            {showDetails ? COPY.hideDetails : COPY.checkDetails}
          </Button>
        )}
      </Stack>
      <Collapse in={showNames} timeout={timeout} unmountOnExit>
        <Box
          component="ul"
          sx={{ m: 0, pl: 2, typography: "s3", color: "text.secondary" }}
        >
          {names.map((name) => (
            <li key={name}>{name}</li>
          ))}
          {held.truncated && <li>…</li>}
        </Box>
      </Collapse>
      <Collapse in={showDetails} timeout={timeout} unmountOnExit>
        <Stack spacing={0.25} sx={{ mt: 0.5 }}>
          {lines.map((one) => (
            <Typography
              key={one.id}
              sx={{ typography: "s3", color: "text.secondary" }}
            >
              {`${one.name}: ${one.text}`}
            </Typography>
          ))}
        </Stack>
      </Collapse>
    </Box>
  );
}

HeldBackBox.propTypes = {
  // The readiness selection for the picked versions and scenarios.
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
