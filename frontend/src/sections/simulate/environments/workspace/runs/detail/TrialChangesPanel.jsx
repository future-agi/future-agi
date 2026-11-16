import PropTypes from "prop-types";
import { Box, Link, Stack, Typography } from "@mui/material";
import TrialDecisions from "./TrialDecisions";
import { TRIAL_CHANGES_COPY as C } from "./trialChanges.constants";

const quiet = {
  typography: "s3",
  color: "text.secondary",
  wordBreak: "break-word",
};

const values = (row) =>
  row
    ? Object.entries(row)
        .map(
          ([key, value]) =>
            `${key}: ${typeof value === "object" ? JSON.stringify(value) : value}`,
        )
        .join(", ")
    : "";

// `outside` replaces "no record changed" when the agent committed changes outside the test world.
function RecordsSection({ records, outside }) {
  const changes = records?.changes || [];
  return (
    <Box data-testid="records-before-after">
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
        {C.recordsHeading}
      </Typography>
      {changes.length === 0 && (
        <Typography
          sx={outside ? { ...quiet, color: "warning.dark" } : quiet}
          data-testid={outside ? "changed-outside-world" : undefined}
        >
          {outside ? outside.message : C.recordsEmpty}
        </Typography>
      )}
      {changes.map((one, index) => (
        <Box key={`${one.table}-${index}`} sx={{ mt: 0.75 }}>
          <Typography sx={{ typography: "s3", fontWeight: 600 }}>
            {`${one.table} ${JSON.stringify(one.key)} · ${
              one.kind === "changed"
                ? C.recordChanged(one.fields || [])
                : C.recordKinds[one.kind] || one.kind
            }`}
          </Typography>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              columnGap: 2,
            }}
          >
            <Typography
              sx={quiet}
            >{`${C.before}: ${values(one.before) || "-"}`}</Typography>
            <Typography
              sx={quiet}
            >{`${C.after}: ${values(one.after) || "-"}`}</Typography>
          </Box>
        </Box>
      ))}
      {(records?.skipped || []).map((one) => (
        <Typography key={`skipped-${one.table}`} sx={quiet}>
          {C.notCompared(one.table, one.why)}
        </Typography>
      ))}
      {records?.truncated > 0 && (
        <Typography sx={quiet}>{C.truncated(records.truncated)}</Typography>
      )}
    </Box>
  );
}

RecordsSection.propTypes = {
  outside: PropTypes.shape({
    code: PropTypes.string,
    message: PropTypes.string,
  }),
  records: PropTypes.shape({
    changes: PropTypes.arrayOf(PropTypes.object),
    skipped: PropTypes.arrayOf(PropTypes.object),
    truncated: PropTypes.number,
  }),
};

// A start that differs is reported among the notices instead.
// Attempted, refused and committed are never summed into one number.
export default function TrialChangesPanel({ trialState }) {
  if (!trialState)
    return <Typography sx={{ ...quiet, p: 2 }}>{C.notRecorded}</Typography>;
  if (trialState.recorded === false)
    return <Typography sx={{ ...quiet, p: 2 }}>{C.inProcess}</Typography>;
  return <WorldState trialState={trialState} />;
}

TrialChangesPanel.propTypes = {
  trialState: PropTypes.object,
};

function WorldState({ trialState }) {
  const changes = trialState.state_changes;
  const decisions = trialState.decisions;
  const preRun = trialState.pre_run;
  const approvals = trialState.approvals;
  const usage = trialState.usage_summary;
  const duplicates = trialState.duplicates;
  // How the trial started, as the server says it; none to say for a start that differed (that is a notice).
  const started = trialState.start?.line;
  // `changed_outside_world` shows with the records, not with the other notices.
  const allNotices = trialState.notices;
  const outside = allNotices.find(
    (one) => one.code === "changed_outside_world",
  );
  const notices = allNotices.filter((one) => one !== outside);
  const { unreadable } = trialState;
  const records = !unreadable && trialState.records ? trialState.records : null;
  const noticeList = notices.length > 0 && (
    <Box>
      <Typography
        sx={{ typography: "s2", fontWeight: 600, color: "warning.dark" }}
      >
        {C.noticesHeading}
      </Typography>
      {notices.map((one, index) => (
        <Typography key={`${one.code}-${index}`} sx={quiet}>
          {one.message}
        </Typography>
      ))}
    </Box>
  );
  const headLines = (
    <>
      {started && <Typography sx={quiet}>{started}</Typography>}
      {unreadable && (
        <Typography sx={{ ...quiet, color: "warning.dark" }}>
          {C.unreadable}
        </Typography>
      )}
      {trialState.end_state_url && (
        <Typography sx={quiet}>
          {C.endStateLead}{" "}
          <Link
            href={trialState.end_state_url}
            target="_blank"
            rel="noopener noreferrer"
            download
          >
            {C.endState}
          </Link>
        </Typography>
      )}
    </>
  );
  if (!changes.length && !decisions.length && !preRun.length)
    return (
      <Stack spacing={1.5} sx={{ p: 2 }}>
        {headLines}
        {noticeList}
        {!unreadable && <Typography sx={quiet}>{C.empty}</Typography>}
        {records && <RecordsSection records={records} outside={outside} />}
      </Stack>
    );
  return (
    <Stack spacing={1.5} sx={{ p: 2 }}>
      <Box>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {C.heading}
        </Typography>
        <Typography sx={quiet}>{C.about}</Typography>
      </Box>
      {headLines}
      {noticeList}
      {duplicates.map((one, index) => (
        <Typography
          key={`duplicate-${index}`}
          sx={{ typography: "s2", fontWeight: 600, color: "error.main" }}
        >
          {C.committedTwice(one.name || one.action)}
        </Typography>
      ))}
      {changes.map((one) => (
        <Box key={one.who}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {one.name}
          </Typography>
          <Typography sx={quiet}>{C.counts(one)}</Typography>
          {one.committed_actions?.length > 0 && (
            <Typography sx={quiet}>
              {C.committed(one.committed_actions)}
            </Typography>
          )}
          {one.read_actions?.length > 0 && (
            <Typography sx={quiet}>{C.read(one.read_actions)}</Typography>
          )}
          {one.answer_lost_actions?.length > 0 && (
            <Typography sx={quiet}>
              {C.answerLost(one.answer_lost_actions)}
            </Typography>
          )}
          {(one.blocked || []).map((call) => (
            <Typography
              key={`blocked-${call.host}`}
              sx={quiet}
              data-testid="blocked-call"
            >
              {C.blocked(call)}
            </Typography>
          ))}
          {one.no_change && !one.committed_actions?.length && (
            <Typography sx={quiet}>{C.noChange}</Typography>
          )}
        </Box>
      ))}
      {records && <RecordsSection records={records} outside={outside} />}
      {preRun.length > 0 && (
        <Box>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {C.preRunHeading}
          </Typography>
          <Typography sx={quiet}>{C.preRunAbout}</Typography>
          {preRun.map((one, index) => (
            <Typography key={`pre-${index}`} sx={quiet}>
              {one.line}
            </Typography>
          ))}
        </Box>
      )}
      <TrialDecisions approvals={approvals} decisions={decisions} />
      {usage.length > 0 && (
        <Box>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {C.usageHeading}
          </Typography>
          {usage.map((line) => (
            <Typography key={`${line.actor}-${line.model}`} sx={quiet}>
              {C.usageBy(
                line.name,
                line.model,
                C.usage(line.calls, line.tokens, line.cost_usd),
              )}
            </Typography>
          ))}
          {trialState.usage_total && (
            <Typography sx={quiet}>
              {C.usageTotal(
                C.usage(
                  trialState.usage_total.calls,
                  trialState.usage_total.tokens,
                  trialState.usage_total.cost_usd,
                ),
              )}
            </Typography>
          )}
        </Box>
      )}
    </Stack>
  );
}

WorldState.propTypes = {
  trialState: PropTypes.shape({
    end_state_url: PropTypes.string,
    state_changes: PropTypes.arrayOf(PropTypes.object),
    decisions: PropTypes.arrayOf(PropTypes.object),
    pre_run: PropTypes.arrayOf(PropTypes.object),
    approvals: PropTypes.arrayOf(PropTypes.object),
    usage_summary: PropTypes.arrayOf(
      PropTypes.shape({
        actor: PropTypes.string,
        name: PropTypes.string,
        model: PropTypes.string,
        calls: PropTypes.number,
        tokens: PropTypes.number,
        cost_usd: PropTypes.number,
      }),
    ),
    usage_total: PropTypes.shape({
      calls: PropTypes.number,
      tokens: PropTypes.number,
      cost_usd: PropTypes.number,
    }),
    records: PropTypes.object,
    start: PropTypes.shape({
      state: PropTypes.string,
      tables: PropTypes.arrayOf(PropTypes.string),
      // How it started, worded by the server; null when there is nothing to say.
      line: PropTypes.string,
    }),
    unreadable: PropTypes.bool,
    recorded: PropTypes.bool,
    duplicates: PropTypes.arrayOf(
      PropTypes.shape({ action: PropTypes.string, name: PropTypes.string }),
    ),
    notices: PropTypes.arrayOf(
      PropTypes.shape({ code: PropTypes.string, message: PropTypes.string }),
    ),
  }),
};
