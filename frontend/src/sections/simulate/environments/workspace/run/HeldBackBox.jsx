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
// The report names up to 200 held scenarios; past these the rest wait one click away.
const SHOWN = 5;

function HeldItems({ items }) {
  return (
    <Stack component="ul" spacing={0.75} sx={{ m: 0, p: 0, listStyle: "none" }}>
      {items.map((one) => (
        <Box component="li" key={one.key} sx={{ minWidth: 0 }}>
          <Typography sx={{ typography: "s1", fontWeight: "fontWeightMedium" }}>
            {one.name}
          </Typography>
          {one.reason && (
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              {one.reason}
            </Typography>
          )}
        </Box>
      ))}
    </Stack>
  );
}
HeldItems.propTypes = {
  items: PropTypes.arrayOf(
    PropTypes.shape({
      key: PropTypes.string,
      name: PropTypes.string,
      reason: PropTypes.string,
    }),
  ).isRequired,
};

// The scenarios a run holds back, from the readiness report: each by name with what holds it. Held ones are never sent.
export default function HeldBackBox({ shownRows = [], selection }) {
  const reduceMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const [showAll, setShowAll] = useState(false);
  const held = selection?.held_back;
  if (!held?.count) return null;
  // The server's words for a row, else ours for a code it sends none for, never the bare code where we have words.
  const reasonText = (row) =>
    selection.reasons?.[row] || COPY.heldCodes[row] || row;
  const byKey = held.by_key ?? {};
  const ownReason = held.reason_by_key ?? {};
  const items = held.keys.map((key) => ({
    key,
    name: held.names?.[key] || key,
    reason: ownReason[key] || (byKey[key] && reasonText(byKey[key])),
  }));
  // A reason no listed scenario carries (past the report's list, or from a report that names none) is still counted.
  const listed = new Set(Object.values(byKey));
  const others = Object.entries(held.by ?? {}).filter(
    ([row, n]) => n > 0 && !listed.has(row) && !shownRows.includes(row),
  );
  const unlisted = held.count - items.length;
  return (
    <Box sx={boxSx} data-testid="run-held-back">
      <Typography
        sx={{ typography: "s1", fontWeight: "fontWeightMedium", mb: 0.75 }}
      >
        {COPY.heldBackTitle(held.count)}
      </Typography>
      <HeldItems items={items.slice(0, SHOWN)} />
      <Collapse
        in={showAll}
        timeout={reduceMotion ? 0 : undefined}
        unmountOnExit
      >
        <Box sx={{ pt: 0.75 }}>
          <HeldItems items={items.slice(SHOWN)} />
        </Box>
      </Collapse>
      {items.length > SHOWN && (
        <Button
          size="small"
          color="inherit"
          sx={{ mt: 0.5, ml: -0.75 }}
          onClick={() => setShowAll((open) => !open)}
        >
          {showAll ? COPY.hideWhich : COPY.showMore(items.length - SHOWN)}
        </Button>
      )}
      {others.map(([row, n]) => (
        <Typography
          key={row}
          sx={{ typography: "s3", color: "text.secondary", mt: 0.5 }}
        >
          {COPY.heldReason(reasonText(row), n)}
        </Typography>
      ))}
      {unlisted > 0 && (
        <Typography sx={{ typography: "s3", color: "text.secondary", mt: 0.5 }}>
          {COPY.heldUnlisted(unlisted)}
        </Typography>
      )}
    </Box>
  );
}

HeldBackBox.propTypes = {
  // The readiness rows Ready to run shows as lines.
  shownRows: PropTypes.arrayOf(PropTypes.string),
  // The report's selection for the picked versions and scenarios.
  selection: PropTypes.shape({
    held_back: PropTypes.shape({
      count: PropTypes.number,
      keys: PropTypes.arrayOf(PropTypes.string),
      names: PropTypes.object,
      by: PropTypes.object,
      // The row each listed scenario is held under, by key.
      by_key: PropTypes.objectOf(PropTypes.string),
      // Its own reason, by key, where it has one; a row's reason is joined from every scenario it holds.
      reason_by_key: PropTypes.objectOf(PropTypes.string),
    }),
    reasons: PropTypes.object,
  }),
};
