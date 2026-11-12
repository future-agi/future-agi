import { useState } from "react";
import PropTypes from "prop-types";
import { Box, Collapse, Link, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";

import { readable } from "./harnessShared";

/**
 * What a run's failure means, in the product's words. The code and the raw text the run
 * recorded stay on the record, behind "Details", for whoever is debugging.
 *
 * A failure that names its cause (`failure.cause`, from a closed set) reads as that cause. One
 * our infrastructure caused reads as ours, not the agent's. Any other failure reads as it did.
 */
export const CAUSE_WORDS = {
  agent_exited_before_ready:
    "Didn't start — the agent exited before it was ready. Fix the crash on startup, then push a new agent version.",
  agent_not_ready_in_time:
    "Didn't start — the agent wasn't listening in time. Make it start faster or listen sooner, then push a new agent version.",
  chat_target_timeout:
    "Stopped — the agent didn't reply to a message in time. Run it again. If it repeats, make the agent reply faster.",
  chat_target_refused:
    "Stopped — the agent refused the connection. Check that it listens on the port it was given, then push a new agent version.",
  unknown: "Stopped — the reason wasn't recorded. Run it again.",
};

export const INFRASTRUCTURE_LINE =
  "Our infrastructure failed, not the agent. Counts neither for nor against it.";

/** The plain headline of a failure, or null when it has none and reads as recorded. */
export function failureHeadline(failure) {
  if (!failure) return null;
  if (failure.cause) return CAUSE_WORDS[failure.cause] || CAUSE_WORDS.unknown;
  if (failure.domain === "infrastructure") return INFRASTRUCTURE_LINE;
  return null;
}

const recordedLine = (failure) =>
  [failure.domain, failure.code].filter(Boolean).map(readable).join(" · ");

export default function RunFailure({ failure, dense }) {
  const [open, setOpen] = useState(false);
  const headline = failureHeadline(failure);
  const text = dense ? "caption" : "body2";

  if (!headline) {
    return (
      <>
        <Typography variant={dense ? "caption" : "subtitle2"}>
          {recordedLine(failure)}
        </Typography>
        {failure.message && (
          <Typography variant={text} component="div">
            {failure.message}
          </Typography>
        )}
      </>
    );
  }

  return (
    <Stack spacing={0.25} sx={{ minWidth: 0 }}>
      <Typography variant={dense ? "caption" : "subtitle2"}>
        {headline}
      </Typography>
      <Link
        component="button"
        type="button"
        variant="caption"
        underline="hover"
        color="text.secondary"
        aria-expanded={open}
        onClick={() => setOpen((wasOpen) => !wasOpen)}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.25,
          alignSelf: "flex-start",
        }}
      >
        Details
        <Iconify
          icon={
            open ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"
          }
          width={14}
        />
      </Link>
      <Collapse in={open} unmountOnExit>
        <Box sx={{ pl: 1, borderLeft: 2, borderColor: "divider" }}>
          <Typography variant="caption" component="div" color="text.secondary">
            {recordedLine(failure)}
            {failure.cause ? ` · ${failure.cause}` : ""}
          </Typography>
          {failure.message && (
            <Typography
              variant="caption"
              component="div"
              color="text.secondary"
              sx={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
            >
              {failure.message}
            </Typography>
          )}
        </Box>
      </Collapse>
    </Stack>
  );
}

RunFailure.propTypes = {
  failure: PropTypes.shape({
    domain: PropTypes.string,
    code: PropTypes.string,
    cause: PropTypes.string,
    message: PropTypes.string,
  }).isRequired,
  dense: PropTypes.bool,
};
