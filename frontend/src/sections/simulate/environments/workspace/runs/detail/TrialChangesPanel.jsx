import PropTypes from "prop-types";
import { Box, Link, Stack, Typography } from "@mui/material";
import { TRIAL_CHANGES_COPY as C } from "./trialChanges.constants";

const quiet = {
  typography: "s3",
  color: "text.secondary",
  wordBreak: "break-word",
};

// A record's values as one short line, for the before and after columns.
const values = (row) =>
  row
    ? Object.entries(row)
        .map(
          ([key, value]) =>
            `${key}: ${typeof value === "object" ? JSON.stringify(value) : value}`,
        )
        .join(", ")
    : "";

// The records the trial added, changed or removed, each with its values before and after. When the agent committed
// changes yet no record of the test world changed, the server's notice says why in place of "no record changed".
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

// Whether the trial started from the seeded records, said first; one that did not is among the notices.
const startLine = (start) =>
  start && start.state !== "differs" ? C.start[start.state] : null;

// A trial's World state per participant (the agent and each actor), and what each actor chose. Kept apart:
// attempted, refused and committed are never summed into one number. A trial with no record says why.
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
  const changes = trialState?.state_changes || [];
  const decisions = trialState?.decisions || [];
  // Messages written before the run: the trial's starting point, shown apart from what actors chose in it.
  const preRun = trialState?.pre_run || [];
  const approvals = trialState?.approvals || [];
  const usage = trialState?.usage_summary || [];
  const duplicates = trialState?.duplicates || [];
  const started = startLine(trialState?.start);
  // How the trial ran that the reader must know, said first: a fault that did not take effect, a start that differed.
  // Changes the agent made outside the test world are said where the records are.
  const allNotices = trialState?.notices || [];
  const outside = allNotices.find(
    (one) => one.code === "changed_outside_world",
  );
  const notices = allNotices.filter((one) => one !== outside);
  const unreadable = trialState?.unreadable || trialState?.records?.unreadable;
  const records =
    trialState?.records && !trialState.records.unreadable
      ? trialState.records
      : null;
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
      {trialState?.end_state_url && (
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
  const names = Object.fromEntries(changes.map((one) => [one.who, one.name]));
  const name = (id) => names[id] || id;
  const timing = (one) => {
    if (one.play === "waiting") return C.waited(one, name);
    if (one.play === "problem") return C.problems[one.problem] || one.why;
    if (one.play !== "timeout") return null;
    if (one.outcome === "fallback_not_committed")
      return C.outcomes.fallback_not_committed(
        C.actionOutcomes[one.action_outcome],
      );
    const said = C.outcomes[one.outcome];
    return typeof said === "function" ? said(name(one.fallback)) : said;
  };
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
          {(one.blocked || []).map((call, index) => (
            <Typography
              key={`blocked-${index}`}
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
              {`${one.name || name(one.actor)}: ${one.arguments?.text || one.action}`}
            </Typography>
          ))}
        </Box>
      )}
      {approvals.length > 0 && (
        <Box>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {C.approvalsHeading}
          </Typography>
          {approvals.map((one, index) => (
            <Typography key={`approval-${index}`} sx={quiet}>
              {`${C.approval(one, name)} · ${one.acted_before_decision ? C.actedBefore : C.waitedFor}`}
            </Typography>
          ))}
        </Box>
      )}
      {decisions.length > 0 && (
        <Box>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {C.decisionsHeading}
          </Typography>
          {decisions.map((one, index) => (
            <Typography key={`${one.actor}-${index}`} sx={quiet}>
              {[
                names[one.actor] || one.actor,
                C.plays[one.play] || one.play,
                timing(one) || one.action || C.nothing,
                one.play !== "timeout" && C.actionOutcomes[one.outcome],
                one.why,
              ]
                .filter(Boolean)
                .join(" · ")}
            </Typography>
          ))}
        </Box>
      )}
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
    // Actors' model use summed per actor and model, and in all.
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
