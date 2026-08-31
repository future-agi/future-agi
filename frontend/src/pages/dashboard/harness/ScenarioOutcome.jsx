import { useState } from "react";
import PropTypes from "prop-types";
import { Box, Collapse, Link, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";
import StatusChip from "src/components/custom-status-chip/CustomStatusChip";
import { STATUS_TYPES } from "src/utils/statusUtils";

import { callSummary } from "./harnessShared";

const chipStatus = (status) => {
  if (status === "passed") return STATUS_TYPES.PASS;
  if (status === "failed" || status === "errored") return STATUS_TYPES.ERROR;
  if (status === "skipped") return STATUS_TYPES.CANCELED;
  return STATUS_TYPES.RUNNING;
};

/**
 * What became of the scenario a timeline row started. The run reports the verdict, the call
 * and the sub-goals apart from the event, so this hangs off the joined outcome rather than
 * the event itself, and renders nothing at all when there is none — a scenario still running,
 * or a sandbox run, which reports neither.
 */
export default function ScenarioOutcome({ outcome }) {
  const [open, setOpen] = useState(false);
  if (!outcome) return null;

  const summary = callSummary(outcome);
  // A check that did not hold carries the sentence explaining why, which is the reason to
  // open this at all. The denominator matters too: one of one reads very differently from
  // five of six. A scenario that passed every check stays closed and quiet.
  const failed = outcome.subGoals.filter((goal) => !goal.held);
  // An outcome can carry nothing worth drawing — a skipped scenario with no call and no
  // checks. Leave the row as it was rather than opening a gap under it.
  if (!outcome.status && !summary && failed.length === 0) return null;

  return (
    <Stack spacing={0.75} sx={{ mt: 0.75 }}>
      <Stack direction="row" alignItems="center" spacing={1}>
        {outcome.status && (
          <StatusChip
            label={outcome.status}
            status={chipStatus(outcome.status)}
            showIcon={false}
          />
        )}
        {summary && (
          <Typography variant="caption" color="text.secondary">
            {summary}
          </Typography>
        )}
        {failed.length > 0 && (
          <Link
            component="button"
            type="button"
            variant="caption"
            underline="hover"
            color="text.secondary"
            onClick={() => setOpen((wasOpen) => !wasOpen)}
            sx={{ display: "inline-flex", alignItems: "center", gap: 0.25 }}
          >
            {failed.length} of {outcome.subGoals.length} checks failed
            <Iconify
              icon={open ? "eva:chevron-up-fill" : "eva:chevron-down-fill"}
              width={14}
            />
          </Link>
        )}
      </Stack>

      <Collapse in={open} unmountOnExit>
        <Stack component="ul" spacing={0.25} sx={{ m: 0, pl: 2 }}>
          {failed.map((goal) => (
            <Box component="li" key={goal.name}>
              <Typography variant="caption" color="text.secondary">
                <Box component="span" sx={{ color: "text.primary" }}>
                  {goal.name}
                </Box>
                {goal.reason ? ` — ${goal.reason}` : ""}
              </Typography>
            </Box>
          ))}
        </Stack>
      </Collapse>
    </Stack>
  );
}

ScenarioOutcome.propTypes = {
  outcome: PropTypes.shape({
    status: PropTypes.string,
    turns: PropTypes.number,
    durationMs: PropTypes.number,
    subGoals: PropTypes.arrayOf(
      PropTypes.shape({
        name: PropTypes.string,
        held: PropTypes.bool,
        reason: PropTypes.string,
      }),
    ),
  }),
};
