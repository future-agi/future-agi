import PropTypes from "prop-types";
import { useState } from "react";
import { Alert, Button, Collapse, Stack, Typography } from "@mui/material";
import { RUN_DIALOG_COPY as COPY } from "../workspace.constants";

export default function RunBlockedLine({ blockedTools, heldBack }) {
  const [open, setOpen] = useState(false);
  if (!blockedTools.length) return null;
  return (
    <Alert
      severity="warning"
      data-testid="run-blocked-tool"
      action={
        <Button
          size="small"
          color="inherit"
          onClick={() => setOpen((shown) => !shown)}
        >
          {open ? COPY.hideDetails : COPY.details}
        </Button>
      }
    >
      {COPY.heldBack(
        heldBack,
        blockedTools.map((entry) => entry.tool),
      )}
      <Collapse in={open}>
        <Stack spacing={0.5} sx={{ mt: 0.75 }}>
          {blockedTools.map((entry) => (
            <Typography key={entry.tool} sx={{ typography: "s2" }}>
              {[
                COPY.blockedTool(entry.tool),
                entry.scenarios.length > 0 &&
                  COPY.usedBy(entry.scenarios.map((one) => one.name)),
              ]
                .filter(Boolean)
                .join(" ")}
            </Typography>
          ))}
        </Stack>
      </Collapse>
    </Alert>
  );
}

RunBlockedLine.propTypes = {
  blockedTools: PropTypes.arrayOf(
    PropTypes.shape({
      tool: PropTypes.string.isRequired,
      scenarios: PropTypes.arrayOf(
        PropTypes.shape({ key: PropTypes.string, name: PropTypes.string }),
      ).isRequired,
    }),
  ).isRequired,
  heldBack: PropTypes.number.isRequired,
};
