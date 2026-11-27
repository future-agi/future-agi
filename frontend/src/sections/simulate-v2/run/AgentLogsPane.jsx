import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import LogViewer from "../components/logs/LogViewer";
import LogExcerpt from "../components/logs/LogExcerpt";
import { agentHostOf } from "../_mock/sandboxLogs";

const HOST_NOTE = {
  platform: (h) => `Its own logs stay in ${h.label}.`,
  endpoint: (h) => `Its own logs stay on ${h.label}.`,
  builtin: () => "It runs on our model, so there are no agent logs.",
};

/* The trial's slice of the run sandbox's log. Trials are processes inside the
   run's one sandbox, not sandboxes of their own. `infraFailed` leads with the
   failing step's excerpt. */
export default function AgentLogsPane({
  trialLog,
  focusLineId,
  padded = true,
  infraFailed = false,
}) {
  const host = trialLog.host || agentHostOf(null);
  const note = HOST_NOTE[host.kind]?.(host);
  return (
    <Stack spacing={1.25} sx={{ p: padded ? 2 : 0 }}>
      <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
        {`Run sandbox ${trialLog.sandbox?.id} · world ${trialLog.span?.world}`}
        {note ? ` · ${note}` : ""}
      </Typography>
      {infraFailed && <LogExcerpt log={trialLog} />}
      <LogViewer
        log={trialLog}
        hideFacts
        compact
        focusLineId={focusLineId}
        maxHeight="calc(100vh - 260px)"
        emptyText="No lines for this trial yet."
      />
    </Stack>
  );
}
AgentLogsPane.propTypes = {
  trialLog: PropTypes.object,
  focusLineId: PropTypes.string,
  padded: PropTypes.bool,
  infraFailed: PropTypes.bool,
};
