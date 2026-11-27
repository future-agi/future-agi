import PropTypes from "prop-types";
import { Box, CircularProgress, Link, Stack, Typography } from "@mui/material";
import { useCallBrowser } from "src/api/simulate-environments/runDetail";
import { ActionLine } from "./ParticipantActions";
import {
  BLOCKED_KINDS,
  QUIET as quiet,
  TRIAL_CHANGES_COPY,
  groupBlocked,
  splitBlocked,
} from "./trialChanges.constants";
import {
  TRIAL_BROWSER_COPY as C,
  browserNotes,
  linkable,
  shotCaption,
} from "./trialBrowser.constants";

function Screenshot({ one, levelKeepsNone }) {
  return (
    <Box
      component="figure"
      data-testid="browser-screenshot"
      data-when={one.when}
      sx={{ m: 0 }}
    >
      {one.kept && one.picture_url ? (
        <Box
          component="img"
          src={one.picture_url}
          alt={C.alt(one.url)}
          loading="lazy"
          sx={{
            display: "block",
            maxWidth: "100%",
            border: 1,
            borderColor: "divider",
            borderRadius: 0.5,
          }}
        />
      ) : levelKeepsNone ? null : (
        <Typography sx={{ ...quiet, fontStyle: "italic" }}>
          {C.notKept}
        </Typography>
      )}
      <Typography
        component="figcaption"
        sx={{ ...quiet, mt: 0.5, overflowWrap: "anywhere" }}
      >
        {shotCaption(one)}
        {one.url ? " · " : null}
        {one.url && linkable(one) ? (
          <Link href={one.url} target="_blank" rel="noopener noreferrer">
            {one.url}
          </Link>
        ) : one.url ? (
          <span>{one.url}</span>
        ) : null}
      </Typography>
    </Box>
  );
}
Screenshot.propTypes = {
  one: PropTypes.object.isRequired,
  levelKeepsNone: PropTypes.bool,
};

function BrowserBlocked({ data }) {
  if (data.blocked_source === "unreadable")
    return <Typography sx={quiet}>{C.blockedUnreadable}</Typography>;
  if (data.blocked_source !== "file" || !data.blocked?.length) return null;
  const blocked = splitBlocked(data.blocked);
  return (
    <Stack spacing={0.5}>
      <Typography sx={{ ...quiet, fontWeight: 600 }}>
        {C.blockedHeading}
      </Typography>
      {BLOCKED_KINDS.filter((kind) => blocked[kind.key].length).map((kind) => (
        <ActionLine
          key={kind.key}
          chipTestId={kind.testId}
          label={TRIAL_CHANGES_COPY[kind.label]}
          names={groupBlocked(blocked[kind.key]).map((call) =>
            TRIAL_CHANGES_COPY.blockedCall(call),
          )}
          tone="warning"
        />
      ))}
      {data.blocked_truncated ? (
        <Typography sx={quiet}>{C.blockedTruncated}</Typography>
      ) : null}
    </Stack>
  );
}
BrowserBlocked.propTypes = { data: PropTypes.object.isRequired };

/* What the trial's browser did, read from the trial's own stored file
   when this entry is opened. The server keeps it for the latest attempt
   only, so an earlier attempt asks nothing. */
export default function TrialBrowserPanel({ callExecutionId, attempt = null }) {
  const { data, isLoading, isError } = useCallBrowser(callExecutionId, {
    enabled: !attempt,
  });
  if (attempt)
    return (
      <Typography sx={{ ...quiet, p: 2 }} data-testid="browser-latest-only">
        {C.latestOnly}
      </Typography>
    );
  if (isLoading)
    return (
      <Stack alignItems="center" sx={{ py: 6 }}>
        <CircularProgress size={20} />
      </Stack>
    );
  if (isError || !data)
    return (
      <Typography
        role="alert"
        sx={{ typography: "s2", color: "error.main", p: 2 }}
      >
        {C.loadFailed}
      </Typography>
    );
  const shots = data.state === "listed" ? data.screenshots || [] : [];
  return (
    <Stack spacing={1.5} sx={{ p: 2 }} data-testid="trial-browser">
      {browserNotes(data).map((note) => (
        <Typography key={note} sx={quiet}>
          {note}
        </Typography>
      ))}
      {shots.map((one, index) => (
        <Screenshot
          key={`${one.at ?? ""}-${index}`}
          one={one}
          levelKeepsNone={data.level_keeps_none}
        />
      ))}
      {data.state === "listed" && data.more > 0 ? (
        <Typography sx={quiet}>{C.more(data.more)}</Typography>
      ) : null}
      <BrowserBlocked data={data} />
    </Stack>
  );
}
TrialBrowserPanel.propTypes = {
  callExecutionId: PropTypes.string.isRequired,
  attempt: PropTypes.string,
};
