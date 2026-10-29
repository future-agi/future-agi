import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { Box, Button, Checkbox, Stack, Typography } from "@mui/material";
import { ConfirmDialog } from "src/components/custom-dialog";
import { clickable, focusRing } from "../components/clickable";

/* Tools whose names say they change something or reach someone — ticked to start. */
const WRITES =
  /(^|_)(issue|send|create|apply|update|delete|cancel|escalate|charge|refund|credit|transfer|book|place|submit|pay|freeze|raise|replace|replacement)(_|$)/i;
const writesSomething = (name) => WRITES.test(name || "");

/**
 * Before a live run, the one thing that must be answered: which of the
 * agent's tools must never run for real. On a live run those are still
 * answered by the environment's world; the rest reach your real systems.
 * Everything else we read from the code stays optional to check.
 */
export default function ToolSafetyDialog({
  open,
  agentName,
  tools,
  marked = null,
  onClose,
  onSave,
}) {
  const [never, setNever] = useState([]);
  useEffect(() => {
    if (open) setNever(marked?.length ? marked : tools.filter(writesSomething));
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const toggle = (t) =>
    setNever((xs) => (xs.includes(t) ? xs.filter((x) => x !== t) : [...xs, t]));
  if (!open) return null;
  return (
    <ConfirmDialog
      open
      onClose={onClose}
      PaperProps={{ sx: { maxWidth: 520 } }}
      title="Tools that must never run for real"
      content={
        <Stack spacing={1.25}>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {`On a live run ${agentName}'s tools reach your real systems. The ones ticked here are always answered by the environment's world instead. Tools that change or send something are ticked to start.`}
          </Typography>
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              maxHeight: 320,
              overflowY: "auto",
            }}
          >
            {tools.map((t, i) => {
              const on = never.includes(t);
              return (
                <Stack
                  key={t}
                  direction="row"
                  alignItems="center"
                  spacing={1}
                  {...clickable(() => toggle(t), {
                    role: "checkbox",
                    checked: on,
                  })}
                  sx={{
                    ...focusRing,
                    px: 1.25,
                    py: 0.625,
                    cursor: "pointer",
                    "&:hover": { bgcolor: "action.hover" },
                    ...(i > 0 && {
                      borderTop: "1px solid",
                      borderColor: "divider",
                    }),
                  }}
                >
                  <Checkbox
                    size="small"
                    checked={on}
                    tabIndex={-1}
                    disableRipple
                    inputProps={{ "aria-hidden": true }}
                    sx={{ p: 0 }}
                  />
                  <Typography
                    sx={{
                      typography: "s2",
                      fontFamily: "monospace",
                      flex: 1,
                      minWidth: 0,
                    }}
                  >
                    {t}
                  </Typography>
                  <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                    {on ? "Never for real" : "Runs for real"}
                  </Typography>
                </Stack>
              );
            })}
          </Box>
        </Stack>
      }
      action={
        <Button
          size="small"
          variant="contained"
          color="primary"
          onClick={() => onSave(never)}
        >
          Save and run live
        </Button>
      }
    />
  );
}

ToolSafetyDialog.propTypes = {
  open: PropTypes.bool,
  agentName: PropTypes.string,
  tools: PropTypes.arrayOf(PropTypes.string).isRequired,
  /* Tools already marked, e.g. at connect. */
  marked: PropTypes.arrayOf(PropTypes.string),
  onClose: PropTypes.func.isRequired,
  onSave: PropTypes.func.isRequired,
};
