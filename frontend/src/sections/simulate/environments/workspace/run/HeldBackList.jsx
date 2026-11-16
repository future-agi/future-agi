import PropTypes from "prop-types";
import { useState } from "react";
import { Button, Stack, Typography } from "@mui/material";

import { useRunSelection } from "src/api/simulate-environments/versions";
import { RUN_DIALOG_COPY as COPY } from "../workspace.constants";

// What the run will hold back and why: one line per reason, and the held-back keys on request.
// One component over one hook, so a fuller readiness panel can take its place.
export function HeldBackView({ selection }) {
  const [shown, setShown] = useState(false);
  const held = selection?.held_back;
  if (!held?.count) return null;
  // The run route acts on the same `next` the preview reads.
  if (selection.next === "run_unvalidated") {
    return (
      <Typography sx={{ typography: "s2", color: "text.primary" }} data-testid="held-back">
        {COPY.runUnvalidated(held.count)}
      </Typography>
    );
  }
  return (
    <Stack spacing={0.5} data-testid="held-back">
      <Typography sx={{ typography: "s2", color: "text.primary" }}>
        {selection.next === "validate_first"
          ? COPY.validateFirst
          : COPY.willRun(selection.running, selection.requested)}
      </Typography>
      {Object.entries(held.by ?? {}).map(([row, count]) => (
        <Typography key={row} sx={{ typography: "s3", color: "text.subtitle" }}>
          {COPY.heldBack(count, selection.reasons?.[row] ?? row)}
        </Typography>
      ))}
      {held.keys?.length > 0 && (
        <>
          <Button
            size="small"
            variant="text"
            sx={{ alignSelf: "flex-start" }}
            onClick={() => setShown((was) => !was)}
          >
            {shown ? COPY.hideCases : COPY.showCases}
          </Button>
          {shown && (
            <Typography sx={{ typography: "s3", color: "text.subtitle" }} data-testid="held-back-keys">
              {held.keys.join(", ")}
            </Typography>
          )}
        </>
      )}
    </Stack>
  );
}

HeldBackView.propTypes = { selection: PropTypes.object };

export default function HeldBackList({ envId, picks, keys, enabled = true }) {
  const { data } = useRunSelection(envId, picks, keys, { enabled });
  return <HeldBackView selection={data} />;
}

HeldBackList.propTypes = {
  envId: PropTypes.string,
  picks: PropTypes.object,
  keys: PropTypes.arrayOf(PropTypes.string),
  enabled: PropTypes.bool,
};
