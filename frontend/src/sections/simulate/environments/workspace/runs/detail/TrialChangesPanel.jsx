import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Link, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import TrialDecisions from "./TrialDecisions";
import {
  ActionChip,
  Callout,
  Count,
  GridTable,
  Section,
} from "./trialChangesParts";
import {
  MONO,
  QUIET as quiet,
  TRIAL_CHANGES_COPY as C,
} from "./trialChanges.constants";

const show = (value) =>
  value === undefined || value === null
    ? "—"
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);

const KIND_TONE = {
  added: "success.dark",
  changed: "info.dark",
  removed: "error.main",
};

/* One record: its table and key, what happened to it, then a Field ·
   Before · After table. Changed fields come first, highlighted; the
   unchanged ones fold away behind a toggle. */
function RecordChange({ one }) {
  const [open, setOpen] = useState(false);
  const fields = [
    ...new Set([
      ...Object.keys(one.before || {}),
      ...Object.keys(one.after || {}),
    ]),
  ];
  const changedSet = new Set(
    one.kind === "changed" ? one.fields || [] : fields,
  );
  const changed = fields.filter((f) => changedSet.has(f));
  const unchanged = fields.filter((f) => !changedSet.has(f));
  const shown = open ? [...changed, ...unchanged] : changed;
  const label =
    one.kind === "changed"
      ? C.recordChanged(one.fields || [])
      : C.recordKinds[one.kind] || one.kind;
  return (
    <Box>
      <Stack
        direction="row"
        alignItems="baseline"
        spacing={1}
        aria-label={`${one.table} ${JSON.stringify(one.key)} · ${label}`}
        sx={{ mb: 0.5, flexWrap: "wrap" }}
      >
        <Typography
          sx={{ typography: "s2", fontWeight: 600, fontFamily: MONO }}
        >
          {`${one.table} ${JSON.stringify(one.key)}`}
        </Typography>
        <Typography
          sx={{
            typography: "s3",
            fontWeight: 500,
            color: KIND_TONE[one.kind] || "text.secondary",
          }}
        >
          {label}
        </Typography>
      </Stack>
      {one.kind !== "changed" ? (
        <GridTable
          cols="minmax(96px, 0.8fr) minmax(0, 2fr)"
          head={[
            C.recordsHead.field,
            one.kind === "removed" ? C.recordsHead.before : C.recordsHead.after,
          ]}
          rows={fields.map((f) => ({
            key: f,
            cells: [
              <Typography
                key="f"
                sx={{
                  typography: "s3",
                  fontFamily: MONO,
                  fontWeight: 500,
                  wordBreak: "break-all",
                }}
              >
                {f}
              </Typography>,
              <Typography
                key="v"
                sx={{
                  ...quiet,
                  fontFamily: MONO,
                  color: "text.primary",
                  textDecoration:
                    one.kind === "removed" ? "line-through" : "none",
                }}
              >
                {show((one.kind === "removed" ? one.before : one.after)?.[f])}
              </Typography>,
            ],
          }))}
        />
      ) : (
        <GridTable
          cols="minmax(96px, 0.8fr) minmax(0, 1fr) minmax(0, 1fr)"
          head={[
            C.recordsHead.field,
            C.recordsHead.before,
            C.recordsHead.after,
          ]}
          rows={shown.map((f) => {
            const isChanged = changedSet.has(f);
            return {
              key: f,
              sx: (theme) =>
                isChanged && one.kind === "changed"
                  ? { bgcolor: alpha(theme.palette.info.main, 0.04) }
                  : {},
              cells: [
                <Typography
                  key="f"
                  sx={{
                    typography: "s3",
                    fontFamily: MONO,
                    fontWeight: isChanged ? 600 : 400,
                    color: isChanged ? "text.primary" : "text.secondary",
                    wordBreak: "break-all",
                  }}
                >
                  {f}
                </Typography>,
                <Typography
                  key="b"
                  sx={{
                    ...quiet,
                    fontFamily: MONO,
                    color: one.before ? "text.secondary" : "text.disabled",
                    textDecoration:
                      isChanged && one.kind !== "added"
                        ? "line-through"
                        : "none",
                  }}
                >
                  {one.before ? show(one.before[f]) : "—"}
                </Typography>,
                <Typography
                  key="a"
                  sx={{
                    ...quiet,
                    fontFamily: MONO,
                    color: one.after
                      ? isChanged
                        ? "text.primary"
                        : "text.secondary"
                      : "text.disabled",
                  }}
                >
                  {one.after ? show(one.after[f]) : "—"}
                </Typography>,
              ],
            };
          })}
        />
      )}
      {one.kind === "changed" && unchanged.length > 0 && (
        <Typography
          component="button"
          type="button"
          onClick={() => setOpen((v) => !v)}
          sx={{
            mt: 0.5,
            p: 0,
            border: 0,
            bgcolor: "transparent",
            cursor: "pointer",
            typography: "s3",
            fontWeight: 500,
            color: "primary.main",
          }}
        >
          {C.unchangedFields(unchanged.length, open)}
        </Typography>
      )}
    </Box>
  );
}
RecordChange.propTypes = { one: PropTypes.object.isRequired };

// `outside` replaces "no record changed" when the agent committed changes outside the test world.
function RecordsSection({ records, outside }) {
  const changes = records?.changes || [];
  return (
    <Section
      title={C.recordsHeading}
      count={changes.length || null}
      data-testid="records-before-after"
    >
      {changes.length === 0 && (
        <Typography
          sx={outside ? { ...quiet, color: "warning.dark" } : quiet}
          data-testid={outside ? "changed-outside-world" : undefined}
        >
          {outside ? outside.message : C.recordsEmpty}
        </Typography>
      )}
      <Stack spacing={1.5}>
        {changes.map((one, index) => (
          <RecordChange key={`${one.table}-${index}`} one={one} />
        ))}
      </Stack>
      {(records?.skipped || []).map((one) => (
        <Typography key={`skipped-${one.table}`} sx={{ ...quiet, mt: 0.75 }}>
          {C.notCompared(one.table, one.why)}
        </Typography>
      ))}
      {records?.truncated > 0 && (
        <Typography sx={{ ...quiet, mt: 0.75 }}>
          {C.truncated(records.truncated)}
        </Typography>
      )}
    </Section>
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

/* Each participant's calls as counts, one row each, with what it committed
   and read as chips under its row. Attempted, refused and committed are
   never summed into one number. */
function CallsSection({ changes }) {
  const dup = changes.some((one) => one.duplicate);
  const cols = `minmax(120px, 1.4fr) repeat(${dup ? 6 : 5}, minmax(56px, 0.6fr))`;
  const H = C.callsHead;
  const head = [
    H.who,
    { label: H.attempted, align: "right" },
    { label: H.committed, align: "right" },
    { label: H.read, align: "right" },
    { label: H.refused, align: "right" },
    { label: H.faulted, align: "right" },
    ...(dup ? [{ label: H.duplicate, align: "right" }] : []),
  ];
  return (
    <Section title={C.callsHeading} hint={C.about}>
      <GridTable
        cols={cols}
        head={head}
        rows={changes.map((one) => ({
          key: one.who,
          label: C.counts(one),
          cells: [
            <Typography
              key="n"
              sx={{
                typography: "s2",
                fontWeight: 500,
                wordBreak: "break-word",
              }}
            >
              {one.name}
            </Typography>,
            <Count key="a" value={one.attempted} />,
            <Count key="c" value={one.committed} tone="text.primary" />,
            <Count key="r" value={one.read} tone="text.secondary" />,
            <Count key="x" value={one.refused} tone="warning.dark" />,
            <Count key="f" value={one.faulted} tone="text.secondary" />,
            ...(dup
              ? [<Count key="d" value={one.duplicate} tone="error.main" />]
              : []),
          ],
          below: <ParticipantActions one={one} />,
        }))}
      />
    </Section>
  );
}
CallsSection.propTypes = { changes: PropTypes.array.isRequired };

function ActionLine({ label, names, tone, sentence, chipTestId }) {
  return (
    <Stack
      aria-label={sentence}
      direction="row"
      spacing={1}
      alignItems="flex-start"
    >
      <Typography
        sx={{
          ...quiet,
          width: 140,
          flexShrink: 0,
          color: "text.disabled",
          pt: 0.25,
        }}
      >
        {label}
      </Typography>
      <Stack
        direction="row"
        useFlexGap
        flexWrap="wrap"
        sx={{ gap: 0.5, minWidth: 0 }}
      >
        {names.map((name, i) => (
          <Box component="span" key={`${name}-${i}`} data-testid={chipTestId}>
            <ActionChip name={name} tone={tone} />
          </Box>
        ))}
      </Stack>
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

function ParticipantActions({ one }) {
  const lines = [];
  if (one.committed_actions?.length)
    lines.push(
      <ActionLine
        key="c"
        label={C.committedLabel}
        sentence={C.committed(one.committed_actions)}
        names={one.committed_actions}
        tone="committed"
      />,
    );
  if (one.read_actions?.length)
    lines.push(
      <ActionLine
        key="r"
        label={C.readLabel}
        sentence={C.read(one.read_actions)}
        names={one.read_actions}
        tone="read"
      />,
    );
  if (one.answer_lost_actions?.length)
    lines.push(
      <ActionLine
        key="t"
        label={C.answerLostLabel}
        sentence={C.answerLost(one.answer_lost_actions)}
        names={one.answer_lost_actions}
        tone="warning"
      />,
    );
  if (one.blocked?.length)
    lines.push(
      <Box key="b">
        <ActionLine
          chipTestId="blocked-call"
          label={C.blockedLabel}
          names={one.blocked.map((call) => C.blockedCall(call))}
          tone="warning"
        />
      </Box>,
    );
  if (one.no_change && !one.committed_actions?.length)
    lines.push(
      <Typography key="n" sx={{ ...quiet, color: "text.disabled" }}>
        {C.noChange}
      </Typography>,
    );
  if (!lines.length) return null;
  return <Stack spacing={0.5}>{lines}</Stack>;
}
ParticipantActions.propTypes = { one: PropTypes.object.isRequired };

// A start that differs is reported among the notices instead.
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
            fontWeight: 500,
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
      <Stack spacing={2} sx={{ p: 2 }}>
        {topLine}
        {callouts}
        {!unreadable && <Typography sx={quiet}>{C.empty}</Typography>}
        {records && <RecordsSection records={records} outside={outside} />}
      </Stack>
    );
  return (
    <Stack spacing={2.5} sx={{ p: 2 }}>
      {topLine}
      {callouts}
      {changes.length > 0 && <CallsSection changes={changes} />}
      {records && <RecordsSection records={records} outside={outside} />}
      <TrialDecisions approvals={approvals} decisions={decisions} />
      {preRun.length > 0 && (
        <Section
          title={C.preRunHeading}
          caption={C.preRunCaption}
          hint={C.preRunAbout}
        >
          <Stack spacing={0.5}>
            {preRun.map((one, index) => (
              <Typography
                key={`pre-${index}`}
                sx={{ ...quiet, color: "text.primary" }}
              >
                {one.line}
              </Typography>
            ))}
          </Stack>
        </Section>
      )}
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
                    sx={{ typography: "s3", fontWeight: 500 }}
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
                          sx={{ typography: "s3", fontWeight: 600 }}
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
                            fontWeight: 600,
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
