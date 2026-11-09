import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { TRIAL_CHANGES_COPY as C } from "./trialChanges.constants";

const quiet = {
  typography: "s3",
  color: "text.secondary",
  wordBreak: "break-word",
};

// Actors' model calls summed per actor and model, then in all: calls, tokens and cost, never just a count.
const usageLines = (rows, name) => {
  const sum = (some) => [
    some.length,
    some.reduce(
      (n, one) => n + (one.tokens_in || 0) + (one.tokens_out || 0),
      0,
    ),
    some.reduce((n, one) => n + (one.cost_usd || 0), 0),
  ];
  const groups = new Map();
  for (const one of rows) {
    const key = `${one.actor}\u0000${one.model || ""}`;
    groups.set(key, [...(groups.get(key) || []), one]);
  }
  const lines = [...groups].map(([key, some]) => ({
    key,
    text: C.usageBy(name(some[0].actor), some[0].model, C.usage(...sum(some))),
  }));
  return [...lines, { key: "all", text: C.usageTotal(C.usage(...sum(rows))) }];
};

// A trial's world changes per participant (the agent and each actor), and what each actor chose. Kept apart:
// attempted, refused and committed are never summed into one number.
export default function TrialChangesPanel({ trialState }) {
  const changes = trialState?.state_changes || [];
  const all = trialState?.decisions || [];
  // A message written before the run is the trial's starting point, shown apart from what actors chose in it.
  const preRun = all.filter((one) => one.play === "history");
  const decisions = all.filter((one) => one.play !== "history");
  const approvals = trialState?.approvals || [];
  const usage = trialState?.usage || [];
  // How the trial ran that the reader must know, e.g. a scenario fault that did not take effect: said first.
  const notices = (trialState?.notices || []).filter((one) => one?.message);
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
  if (!changes.length && !all.length)
    return notices.length ? (
      <Stack spacing={1.5} sx={{ p: 2 }}>
        {noticeList}
        <Typography sx={quiet}>{C.empty}</Typography>
      </Stack>
    ) : (
      <Typography sx={{ ...quiet, p: 2 }}>{C.empty}</Typography>
    );
  const names = Object.fromEntries(changes.map((one) => [one.who, one.name]));
  const name = (id) => names[id] || id;
  const timing = (one) => {
    if (one.play === "waiting") return C.waited(one, name);
    if (one.play === "problem") return C.problems[one.problem] || one.why;
    if (one.play !== "timeout") return null;
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
      {noticeList}
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
          {one.no_change && !one.committed_actions?.length && (
            <Typography sx={quiet}>{C.noChange}</Typography>
          )}
        </Box>
      ))}
      {preRun.length > 0 && (
        <Box>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {C.preRunHeading}
          </Typography>
          <Typography sx={quiet}>{C.preRunAbout}</Typography>
          {preRun.map((one, index) => (
            <Typography key={`pre-${index}`} sx={quiet}>
              {`${name(one.actor)}: ${one.arguments?.text || one.action}`}
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
          {usageLines(usage, name).map((line) => (
            <Typography key={line.key} sx={quiet}>
              {line.text}
            </Typography>
          ))}
        </Box>
      )}
    </Stack>
  );
}

TrialChangesPanel.propTypes = {
  trialState: PropTypes.shape({
    state_changes: PropTypes.arrayOf(PropTypes.object),
    decisions: PropTypes.arrayOf(PropTypes.object),
    approvals: PropTypes.arrayOf(PropTypes.object),
    usage: PropTypes.arrayOf(PropTypes.object),
    notices: PropTypes.arrayOf(
      PropTypes.shape({ code: PropTypes.string, message: PropTypes.string }),
    ),
  }),
};
