import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Collapse, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { clickable, focusRing } from "../components/clickable";
import { isBadOutcome } from "../_mock/actorEvents";

const MONO = "ui-monospace, Menlo, monospace";

/**
 * A world actor's action in the transcript, at the time it happened:
 * "Manager · approver", what they did and how it went. The raw arguments,
 * result and error are folded behind a click.
 */
export default function ActorEventRow({ event, time }) {
  const [open, setOpen] = useState(false);
  const bad = isBadOutcome(event.outcome);
  const raw = JSON.stringify(
    {
      arguments: event.args,
      ...(event.result && { result: event.result }),
      ...(event.error && { error: event.error }),
    },
    null,
    2,
  );
  return (
    <Box sx={{ px: 1.75, py: 0.5 }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        {...clickable(() => setOpen((o) => !o), {
          expanded: open,
          label: `${event.name}, ${event.outcome} ${event.action}`,
        })}
        sx={{ ...focusRing, cursor: "pointer", minWidth: 0 }}
      >
        <Iconify
          icon="solar:alt-arrow-right-linear"
          width={12}
          sx={{
            color: "text.disabled",
            flexShrink: 0,
            transform: open ? "rotate(90deg)" : "none",
          }}
        />
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            fontFamily: MONO,
            flexShrink: 0,
          }}
        >
          {time}
        </Typography>
        <Typography noWrap sx={{ typography: "s2", minWidth: 0 }}>
          <Box component="span" sx={{ fontWeight: 600 }}>
            {`${event.name} · ${event.role}`}
          </Box>
          <Box component="span" sx={{ color: "text.tertiary" }}>
            {" — "}
          </Box>
          <Box
            component="span"
            sx={{ color: bad ? "status.danger.fg" : "text.secondary" }}
          >
            {event.outcome}
          </Box>{" "}
          <Box component="span" sx={{ fontFamily: MONO }}>
            {event.action}
          </Box>
        </Typography>
      </Stack>
      <Collapse in={open} unmountOnExit>
        <Box
          component="pre"
          sx={{
            m: 0,
            mt: 0.5,
            ml: 3,
            p: 1,
            borderRadius: 1,
            bgcolor: "background.neutral",
            typography: "s3",
            fontFamily: MONO,
            color: "text.secondary",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {raw}
        </Box>
      </Collapse>
    </Box>
  );
}
ActorEventRow.propTypes = {
  event: PropTypes.shape({
    name: PropTypes.string,
    role: PropTypes.string,
    action: PropTypes.string,
    outcome: PropTypes.string,
    args: PropTypes.object,
    result: PropTypes.object,
    error: PropTypes.string,
  }).isRequired,
  time: PropTypes.string,
};
