import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import {
  QUIET as quiet,
  TRIAL_CHANGES_COPY as C,
  groupBlocked,
  groupNames,
  ACTION_TONE,
} from "./trialChanges.constants";

/* A label, then the calls as a plain list in call order, repeats counted. */
export function ActionLine({ label, names, tone, sentence, chipTestId }) {
  return (
    <Stack
      aria-label={sentence}
      direction="row"
      spacing={1}
      alignItems="baseline"
    >
      <Typography sx={{ ...quiet, width: 140, flexShrink: 0 }}>
        {label}
      </Typography>
      <Typography
        sx={{
          ...quiet,
          minWidth: 0,
          color: ACTION_TONE[tone] || "text.primary",
        }}
      >
        {names.map((name, i) => (
          <span key={`${name}-${i}`}>
            {i > 0 && ", "}
            <span data-testid={chipTestId}>{name}</span>
          </span>
        ))}
      </Typography>
    </Stack>
  );
}
ActionLine.propTypes = {
  label: PropTypes.string.isRequired,
  names: PropTypes.arrayOf(PropTypes.string).isRequired,
  tone: PropTypes.string,
  sentence: PropTypes.string,
  chipTestId: PropTypes.string,
};

export function ParticipantActions({ one }) {
  const lines = [];
  if (one.committed_actions?.length)
    lines.push(
      <ActionLine
        key="c"
        label={C.committedLabel}
        sentence={C.committed(one.committed_actions)}
        names={groupNames(one.committed_actions)}
        tone="committed"
      />,
    );
  if (one.read_actions?.length)
    lines.push(
      <ActionLine
        key="r"
        label={C.readLabel}
        sentence={C.read(one.read_actions)}
        names={groupNames(one.read_actions)}
        tone="read"
      />,
    );
  if (one.answer_lost_actions?.length)
    lines.push(
      <ActionLine
        key="t"
        label={C.answerLostLabel}
        sentence={C.answerLost(one.answer_lost_actions)}
        names={groupNames(one.answer_lost_actions)}
        tone="warning"
      />,
    );
  if (one.blocked?.length)
    lines.push(
      <Box key="b">
        <ActionLine
          chipTestId="blocked-call"
          label={C.blockedLabel}
          names={groupBlocked(one.blocked).map((call) => C.blockedCall(call))}
          tone="warning"
        />
      </Box>,
    );
  if (one.refused_actions?.length)
    lines.push(
      <Box key="x" data-testid="refused-attempts">
        <Typography sx={{ ...quiet, fontWeight: 600 }}>
          {C.refusedHeading}
        </Typography>
        {one.refused_actions.map((refused, index) => (
          <Typography key={`${refused.action}-${index}`} sx={quiet}>
            {C.refusedAttempt(refused)}
          </Typography>
        ))}
      </Box>,
    );
  if (one.no_change && !one.committed_actions?.length)
    lines.push(
      <Typography key="n" sx={quiet}>
        {C.noChange}
      </Typography>,
    );
  if (!lines.length) return null;
  return <Stack spacing={0.5}>{lines}</Stack>;
}
ParticipantActions.propTypes = { one: PropTypes.object.isRequired };
