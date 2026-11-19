import PropTypes from "prop-types";
import { Link, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { Callout, Count, GridTable, Section } from "./trialChangesParts";
import {
  MONO,
  QUIET as quiet,
  TRIAL_CHANGES_COPY as C,
} from "./trialChanges.constants";

import TrialDecisions from "./TrialDecisions";
import { CallsSection } from "./CallsSection";
import { PreRunSection } from "./PreRunSection";
import { RecordsSection } from "./RecordChange";

// A start that differs is reported among the notices instead.
// Same insets as the Transcript tab in the drawer.
const ROOT_SX = { px: 2, pt: 1.25, pb: 2 };

export default function TrialChangesPanel({ trialState }) {
  if (!trialState)
    return <Typography sx={{ ...quiet, p: 2 }}>{C.notRecorded}</Typography>;
  if (trialState.recorded === false)
    return (
      <Typography sx={{ ...quiet, p: 2 }}>{trialState.why_label}</Typography>
    );
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

  const topLine = (started || trialState.end_state_url) && (
    <Stack
      direction="row"
      alignItems="center"
      justifyContent="space-between"
      spacing={2}
      // A one-line caption: it sits closer to what follows than a section does.
      sx={{ mb: -1 }}
    >
      <Typography sx={quiet}>{started || ""}</Typography>
      {trialState.end_state_url && (
        <Link
          href={trialState.end_state_url}
          target="_blank"
          rel="noopener noreferrer"
          download
          title={C.endStateHint}
          sx={{
            typography: "s3",
            fontWeight: "fontWeightMedium",
            display: "inline-flex",
            alignItems: "center",
            gap: 0.5,
            flexShrink: 0,
          }}
        >
          <Iconify icon="solar:download-minimalistic-linear" width={14} />
          {C.endState}
        </Link>
      )}
    </Stack>
  );
  const callouts = (
    <>
      {unreadable && <Callout tone="warning" title={C.unreadable} lines={[]} />}
      {duplicates.length > 0 && (
        <Callout
          tone="error"
          title={duplicates
            .map((one) => C.committedTwice(one.name || one.action))
            .join(" · ")}
          lines={[]}
        />
      )}
      {notices.length > 0 && (
        <Callout
          tone="warning"
          title={C.noticesHeading}
          lines={notices.map((one) => one.message)}
        />
      )}
    </>
  );

  if (!changes.length && !decisions.length && !preRun.length)
    return (
      <Stack spacing={2} sx={ROOT_SX}>
        {topLine}
        {callouts}
        {!unreadable && <Typography sx={quiet}>{C.empty}</Typography>}
        {records && <RecordsSection records={records} outside={outside} />}
      </Stack>
    );
  return (
    <Stack spacing={2} sx={ROOT_SX}>
      {topLine}
      {callouts}
      {changes.length > 0 && <CallsSection changes={changes} />}
      {records && <RecordsSection records={records} outside={outside} />}
      <TrialDecisions approvals={approvals} decisions={decisions} />
      {preRun.length > 0 && <PreRunSection entries={preRun} />}
      {usage.length > 0 && (
        <Section title={C.usageHeading} caption={C.usageNote}>
          <GridTable
            cols="minmax(96px, 1fr) minmax(96px, 1.2fr) repeat(3, minmax(56px, 0.5fr))"
            head={[
              C.usageHead.who,
              C.usageHead.model,
              { label: C.usageHead.calls, align: "right" },
              { label: C.usageHead.tokens, align: "right" },
              { label: C.usageHead.cost, align: "right" },
            ]}
            rows={[
              ...usage.map((line) => ({
                key: `${line.actor}-${line.model}`,
                label: C.usageBy(
                  line.name,
                  line.model,
                  C.usage(line.calls, line.tokens, line.cost_usd),
                ),
                cells: [
                  <Typography
                    key="w"
                    sx={{ typography: "s3", fontWeight: "fontWeightMedium" }}
                  >
                    {line.name}
                  </Typography>,
                  <Typography key="m" sx={{ ...quiet, fontFamily: MONO }}>
                    {line.model || "—"}
                  </Typography>,
                  <Count key="c" value={line.calls} />,
                  <Count key="t" value={line.tokens} />,
                  <Typography
                    key="$"
                    sx={{
                      typography: "s2",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {`$${line.cost_usd.toFixed(4)}`}
                  </Typography>,
                ],
              })),
              ...(trialState.usage_total
                ? [
                    {
                      key: "total",
                      label: C.usageTotal(
                        C.usage(
                          trialState.usage_total.calls,
                          trialState.usage_total.tokens,
                          trialState.usage_total.cost_usd,
                        ),
                      ),
                      sx: { bgcolor: "background.neutral" },
                      cells: [
                        <Typography
                          key="w"
                          sx={{
                            typography: "s3",
                            fontWeight: "fontWeightSemiBold",
                          }}
                        >
                          {C.usageTotalLabel}
                        </Typography>,
                        <span key="m" />,
                        <Count key="c" value={trialState.usage_total.calls} />,
                        <Count key="t" value={trialState.usage_total.tokens} />,
                        <Typography
                          key="$"
                          sx={{
                            typography: "s2",
                            fontWeight: "fontWeightSemiBold",
                            fontVariantNumeric: "tabular-nums",
                          }}
                        >
                          {`$${trialState.usage_total.cost_usd.toFixed(4)}`}
                        </Typography>,
                      ],
                    },
                  ]
                : []),
            ]}
          />
        </Section>
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
