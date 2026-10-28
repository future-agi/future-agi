import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";

import {
  NudgeActions,
  NudgeStatus,
} from "../../buildEnvironment/console/NudgeCard";
import { AGENT_VERSIONS_COPY } from "../workspace.constants";

const COPY = AGENT_VERSIONS_COPY.nudge;

// What the nudge would rebuild for, in counts: files, variables, prompt and
// settings. Names stay in the chat card.
const summary = (facts = {}) => {
  const files = facts.changedFiles?.length || 0;
  const variables = Object.values(facts.variables || {}).reduce(
    (total, names) => total + (names?.length || 0),
    0,
  );
  return [
    files && COPY.files(files),
    variables && COPY.variables(variables),
    facts.promptChanged && COPY.prompt,
    facts.settingsChanged?.length && COPY.settings,
  ]
    .filter(Boolean)
    .join(" · ");
};

// The environment nudge on its trigger version's row: a compact summary with
// Rebuild and Skip, then its progress with Stop, and what it did. Answered
// through the same endpoints as the chat card.
export default function RebuildAction({
  nudge,
  label,
  nudgesEnabled,
  answering,
  rebuilding,
  stopping,
  focus,
  error,
  onRebuild,
  onSkipNudge,
  onStopNudge,
}) {
  const pending = nudge.state === "pending";
  const counts = pending && summary(nudge.facts);
  return (
    <Box
      data-testid={`rebuild-action-${nudge.id}`}
      sx={{ mt: 1, pt: 1, borderTop: "1px solid", borderColor: "divider" }}
    >
      <Typography sx={{ typography: "s3", fontWeight: "fontWeightBold" }}>
        {COPY.title}
      </Typography>
      {counts && (
        <Typography sx={{ typography: "s3", color: "text.secondary", mb: 1 }}>
          {counts}
        </Typography>
      )}
      {!pending && (
        <NudgeStatus nudge={nudge} focus={focus} sx={{ typography: "s3" }} />
      )}
      <NudgeActions
        nudge={nudge}
        subject={label}
        nudgesEnabled={nudgesEnabled}
        answering={answering}
        rebuilding={rebuilding}
        stopping={stopping}
        error={error}
        onRebuild={onRebuild}
        onSkipNudge={onSkipNudge}
        onStopNudge={onStopNudge}
      />
    </Box>
  );
}

RebuildAction.propTypes = {
  nudge: PropTypes.shape({
    id: PropTypes.string.isRequired,
    state: PropTypes.string,
    facts: PropTypes.object,
  }).isRequired,
  label: PropTypes.string.isRequired,
  nudgesEnabled: PropTypes.bool,
  answering: PropTypes.bool,
  rebuilding: PropTypes.bool,
  stopping: PropTypes.bool,
  // Its state line takes focus once its own answer or Stop lands.
  focus: PropTypes.bool,
  error: PropTypes.object,
  onRebuild: PropTypes.func,
  onSkipNudge: PropTypes.func.isRequired,
  onStopNudge: PropTypes.func,
};
