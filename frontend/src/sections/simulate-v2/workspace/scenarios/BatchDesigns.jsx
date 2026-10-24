import PropTypes from "prop-types";
import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  IconButton,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import ScenarioTable from "./ScenarioTable";
import { relativeTime, sourceOf } from "../../_mock/scenarioProvenance";
import { ENV_CHANGES } from "../../_mock/versions";
import { displayEnvLabel } from "../../_mock/envModel";
import { clickable, focusRing } from "../../components/clickable";

const ENV_CHANGE_LABEL = Object.fromEntries(
  ENV_CHANGES.map((c) => [c.id, c.label]),
);

/**
 * How the Scenarios tab shows batches.
 *
 * A batch is one addition to the suite — the scenarios the build wrote, then
 * each later drop: you adding a few by hand, a teammate importing a dataset,
 * the builder chat writing more. Each batch is its own container: who added
 * it and when across the top, its scenarios (under the current Group by) in
 * a table inside. Every column stays, and the table / list toggle applies.
 *
 * A history drawer sits beside the containers, the way Figma shows
 * version history: every batch (and every run) on one timeline, newest
 * first. Picking a batch jumps to its container; "View as of" shows the
 * suite as it stood then — what a past run was actually tested against.
 */

/* ── shared ─────────────────────────────────────────────────────────────── */

const HOW = {
  derived: "when the environment was built",
  template: "from a template",
  manual: "by hand",
  "builder-chat": "in chat",
  chat: "in chat",
  generated: "generated",
  "error-feed": "from the Error Feed",
  "failing-case": "from failing cases",
  "persona-library": "from the persona library",
  "dataset-import": "from a dataset",
  rebuild: "by a rebuild",
  production: "from production traces",
};

const who = (b) => b.meta.addedBy?.name || "System";
const plural = (n) => `${n} scenario${n === 1 ? "" : "s"}`;
/* "Arjun Kapoor added 3 scenarios" / "System proposed 4 scenarios" / "Vel built the environment". */
const headline = (b) =>
  b.meta.source === "derived"
    ? `${who(b)} built the environment`
    : `${who(b)} ${b.rows.length && b.rows.every((r) => r.proposed) ? "proposed" : "added"} ${plural(b.rows.length)}`;
const howAndWhen = (b) =>
  [
    b.meta.source === "derived"
      ? b.rows.some((r) => r.fromDescription)
        ? "written from your description"
        : "written from the agent"
      : HOW[b.meta.source],
    b.meta.note,
    relativeTime(b.meta.addedAt),
  ]
    .filter(Boolean)
    .join(" · ");
const cap = (str) => str.charAt(0).toUpperCase() + str.slice(1);
const stampOf = (iso) =>
  iso
    ? new Date(iso).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "";

function Avatar({ batch, size = 22 }) {
  const name = who(batch);
  const initials = name
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0])
    .join("")
    .toUpperCase();
  return (
    <Box
      sx={{
        width: size,
        height: size,
        borderRadius: "50%",
        flexShrink: 0,
        display: "grid",
        placeItems: "center",
        bgcolor: "action.selected",
        fontSize: size * 0.42,
        fontWeight: 600,
        color: "text.primary",
      }}
    >
      {initials}
    </Box>
  );
}
Avatar.propTypes = { batch: PropTypes.object, size: PropTypes.number };

function BatchBody({ batch, view, tableProps, renderList }) {
  return view === "table" ? (
    <ScenarioTable
      {...tableProps}
      rows={batch.rows}
      groups={batch.innerGroups}
    />
  ) : (
    renderList(batch.innerGroups)
  );
}
BatchBody.propTypes = {
  batch: PropTypes.object,
  view: PropTypes.string,
  tableProps: PropTypes.object,
  renderList: PropTypes.func,
};

function Tag({ children }) {
  return <Chip size="small" label={children} sx={{ flexShrink: 0 }} />;
}
Tag.propTypes = { children: PropTypes.node };

/* ── v8 · containers ────────────────────────────────────────────────────── */

/**
 * One group per batch, newest first. Design system table: no box around it —
 * the header (who added the batch, how and when, what is in it) sits on the
 * page and the batch's scenarios follow beneath a single rule, rows divided
 * by hairlines. Air between groups separates one addition from the next.
 */
function BatchContainer({
  batch,
  latest,
  view,
  tableProps,
  renderList,
  flash,
}) {
  const [open, setOpen] = useState(true);
  return (
    <Box
      id={`batch-${batch.meta.batchId}`}
      sx={{
        /* Clear of the sticky toolbar when the history jumps here. */
        scrollMarginTop: "calc(var(--scn-head, 64px) + 8px)",
      }}
    >
      {/* The counts and actions drop to a second line when the pane is
          narrow, rather than squeezing the headline to nothing. */}
      <Stack
        direction="row"
        alignItems="center"
        onClick={() => setOpen((v) => !v)}
        sx={{
          px: 1.5,
          py: 1.25,
          cursor: "pointer",
          minWidth: 0,
          flexWrap: "wrap",
          columnGap: 1.5,
          rowGap: 1,
          borderRadius: 1,
          transition: "background-color 200ms",
          /* Picked in the history — a brief tint so the eye lands on it. */
          ...(flash && { bgcolor: "action.selected" }),
        }}
      >
        <Avatar batch={batch} size={30} />
        <Box sx={{ flex: "1 1 220px", minWidth: 0 }}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography
              noWrap
              sx={{ typography: "s1", fontWeight: 600, lineHeight: 1.3 }}
            >
              {headline(batch)}
            </Typography>
            {latest && <Tag>Latest</Tag>}
          </Stack>
          {/* How it came in, with its mark, and when — the exact time on hover. */}
          <Tooltip
            arrow
            placement="bottom-start"
            title={`${sourceOf(batch.meta.source).label} · ${stampOf(batch.meta.addedAt)}`}
          >
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.625}
              sx={{
                mt: 0.25,
                width: "fit-content",
                maxWidth: "100%",
                color: "text.tertiary",
              }}
            >
              <Iconify
                icon={sourceOf(batch.meta.source).icon}
                width={12}
                sx={{ flexShrink: 0 }}
              />
              <Typography noWrap sx={{ typography: "s3", color: "inherit" }}>
                {cap(howAndWhen(batch))}
              </Typography>
            </Stack>
          </Tooltip>
        </Box>
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{ ml: "auto" }}
        >
          {/* The same Hide / Show control as the timeline (v1) — an outlined
            button that says how many it folds away. */}
          <Stack
            direction="row"
            alignItems="center"
            spacing={0.75}
            {...clickable(
              (e) => {
                e.stopPropagation();
                setOpen((v) => !v);
              },
              { expanded: open },
            )}
            sx={{
              ...focusRing,
              px: 1.25,
              py: 0.5,
              borderRadius: "6px",
              flexShrink: 0,
              cursor: "pointer",
              border: "1px solid",
              borderColor: "divider",
              bgcolor: "background.paper",
              transition: "border-color 120ms, background-color 120ms",
              "&:hover": {
                borderColor: (t) => alpha(t.palette.text.primary, 0.4),
                bgcolor: "action.hover",
              },
            }}
          >
            <Typography
              sx={{
                typography: "s2",
                fontWeight: 600,
                fontSize: 12,
                color: "text.primary",
                whiteSpace: "nowrap",
              }}
            >
              {open
                ? `Hide ${batch.rows.length}`
                : `Show ${batch.rows.length} scenarios`}
            </Typography>
            <Iconify
              icon={
                open
                  ? "solar:alt-arrow-up-linear"
                  : "solar:alt-arrow-down-linear"
              }
              width={14}
              sx={{ color: "text.tertiary" }}
            />
          </Stack>
        </Stack>
      </Stack>
      {open && (
        <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
          <BatchBody
            batch={batch}
            view={view}
            tableProps={tableProps}
            renderList={renderList}
          />
        </Box>
      )}
    </Box>
  );
}
BatchContainer.propTypes = {
  batch: PropTypes.object,
  latest: PropTypes.bool,
  view: PropTypes.string,
  tableProps: PropTypes.object,
  renderList: PropTypes.func,
  flash: PropTypes.bool,
};

export function BatchContainers({
  batches,
  view,
  tableProps,
  renderList,
  flashId,
  latestId,
}) {
  return (
    <Stack spacing={3}>
      {batches.map((b, i) => (
        <BatchContainer
          key={b.meta.batchId}
          batch={b}
          latest={
            latestId
              ? b.meta.batchId === latestId
              : i === 0 && batches.length > 1
          }
          view={view}
          tableProps={tableProps}
          renderList={renderList}
          flash={flashId === b.meta.batchId}
        />
      ))}
    </Stack>
  );
}
BatchContainers.propTypes = {
  batches: PropTypes.array,
  view: PropTypes.string,
  tableProps: PropTypes.object,
  renderList: PropTypes.func,
  flashId: PropTypes.string,
  latestId: PropTypes.string,
};

/* ── v9 · history panel ─────────────────────────────────────────────────── */

const DAY = 24 * 3600 * 1000;
/* "Today, 17:00" · "Yesterday, 09:00" · "22 Sept, 21:13" — how a history reads. */
const whenLabel = (iso) => {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const time = d.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  });
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  if (d >= startOfToday) return `Today, ${time}`;
  if (d >= new Date(startOfToday.getTime() - DAY)) return `Yesterday, ${time}`;
  return `${d.toLocaleDateString(undefined, { day: "numeric", month: "short" })}, ${time}`;
};

/* Every history row: inset and rounded, so the selected one reads as a soft
   highlight rather than a bar across the drawer. The dot sits at
   HISTORY_LINE_X so the line runs through each one. */
const historyRowSx = (selected, connect) => ({
  position: "relative",
  mx: 1,
  px: 1.5,
  py: 1.25,
  borderRadius: 1,
  cursor: "pointer",
  bgcolor: selected ? "action.selected" : "transparent",
  "&:hover": { bgcolor: selected ? "action.selected" : "action.hover" },
  /* The line down to the next entry's dot — drawn per row, so it starts and
     ends exactly on a dot however tall the rows are. */
  ...(connect && {
    "&::after": {
      content: '""',
      position: "absolute",
      left: 17,
      top: 20,
      height: "100%",
      width: "1px",
      bgcolor: "divider",
    },
  }),
});

/* A point on the history line — round for a batch, square for a run, a
   diamond for a new environment version. */
function HistoryDot({ square, diamond, active }) {
  return (
    <Box
      sx={{
        width: 11,
        height: 11,
        borderRadius: square || diamond ? 0.5 : "50%",
        flexShrink: 0,
        mt: "4px",
        border: "2px solid",
        borderColor: active || diamond ? "text.primary" : "text.disabled",
        bgcolor: active ? "text.primary" : "background.paper",
        position: "relative",
        zIndex: 1,
        ...(diamond && { transform: "rotate(45deg) scale(0.85)" }),
      }}
    />
  );
}
HistoryDot.propTypes = {
  square: PropTypes.bool,
  diamond: PropTypes.bool,
  active: PropTypes.bool,
};

/**
 * Every addition to the suite, and every run, on one timeline — newest
 * first; the header carries the suite's total. `batches` are the raw batch records
 * (newest first); `runs` carry a time and the scenarios they used.
 */
export function BatchHistoryPanel({
  batches,
  runs = [],
  versions = [],
  total,
  asOf,
  onPick,
  onViewAsOf,
  onClose,
}) {
  const [hover, setHover] = useState(null);
  const at = (iso) => Date.parse(iso || "") || 0;
  /* The newest batch at or before a moment — what a run was tested against. */
  const batchAt = (iso) => batches.find((b) => at(b.addedAt) <= at(iso));
  const entries = [
    ...batches.map((m) => ({
      kind: "batch",
      id: m.batchId,
      t: at(m.addedAt),
      m,
    })),
    ...runs.map((r, i) => ({
      kind: "run",
      id: r.id,
      t: at(r.finishedAt || r.startedAt),
      r,
      no: i + 1,
    })),
    /* Every environment version after the first — each one changes the test
       every later run takes, so it sits on the same line as the runs. */
    ...versions
      .filter((v) => v.label !== "v1" && at(v.createdAt))
      .map((v) => ({
        kind: "version",
        id: `env-${v.label}`,
        t: at(v.createdAt),
        v,
      })),
  ].sort((a, b) => b.t - a.t);
  const runNo = new Map(
    [...runs]
      .sort(
        (a, b) =>
          at(a.finishedAt || a.startedAt) - at(b.finishedAt || b.startedAt),
      )
      .map((r, i) => [r.id, i + 1]),
  );

  return (
    /* Fills the history drawer, full height, like Figma's version history. */
    <Box
      sx={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        sx={{
          px: 2.5,
          py: 1.5,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s1", fontWeight: 600 }}>
            Scenario history
          </Typography>
          {/* The suite's size now — what every new run uses — then how it
              got there. While an older point is shown, the way back. */}
          <Stack
            direction="row"
            alignItems="center"
            spacing={0.75}
            sx={{ mt: 0.25 }}
          >
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              <Box
                component="span"
                sx={{ color: "text.primary", fontWeight: 600 }}
              >{`${total} scenarios`}</Box>
              {` · ${batches.length} batch${batches.length === 1 ? "" : "es"} · newest first`}
            </Typography>
            {asOf && (
              <Typography
                {...clickable(() => onViewAsOf(null), { role: "link" })}
                sx={{
                  ...focusRing,
                  typography: "s3",
                  fontWeight: 600,
                  color: "text.secondary",
                  cursor: "pointer",
                  "&:hover": {
                    color: "text.primary",
                    textDecoration: "underline",
                  },
                }}
              >
                · Back to current
              </Typography>
            )}
          </Stack>
        </Box>
        <Tooltip arrow title="Close history">
          <IconButton size="small" onClick={onClose}>
            <Iconify icon="solar:close-linear" width={20} />
          </IconButton>
        </Tooltip>
      </Stack>

      <Box sx={{ flex: 1, minHeight: 0, overflowY: "auto", py: 1 }}>
        <Box sx={{ position: "relative" }}>
          {entries.map((e, idx) => {
            const connect = idx < entries.length - 1;
            if (e.kind === "version") {
              const changed = (e.v.changed || [])
                .map((c) => ENV_CHANGE_LABEL[c])
                .filter(Boolean);
              return (
                <Stack
                  key={e.id}
                  direction="row"
                  spacing={1.25}
                  sx={{
                    ...historyRowSx(false, connect),
                    cursor: "default",
                    "&:hover": {},
                  }}
                >
                  <HistoryDot diamond />
                  <Box minWidth={0} flex={1}>
                    <Typography
                      sx={{ typography: "s3", color: "text.tertiary" }}
                    >
                      {whenLabel(e.v.createdAt)}
                    </Typography>
                    <Typography
                      sx={{ typography: "s2", fontWeight: 600 }}
                    >{`Environment ${e.v.shownAs || displayEnvLabel(e.v.label, versions)}`}</Typography>
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.tertiary",
                        mt: 0.25,
                      }}
                    >
                      {[changed.join(" · "), e.v.note]
                        .filter(Boolean)
                        .join(" — ")}
                    </Typography>
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.disabled",
                        mt: 0.25,
                      }}
                    >
                      {"Runs before and after this aren't the same test"}
                    </Typography>
                  </Box>
                </Stack>
              );
            }
            if (e.kind === "run") {
              const seen = batchAt(e.r.startedAt || e.r.finishedAt);
              const n = e.r.scenarioIds?.length;
              return (
                <Stack
                  key={`run-${e.id}`}
                  direction="row"
                  spacing={1.25}
                  {...clickable(
                    seen ? () => onViewAsOf(seen.batchId) : undefined,
                  )}
                  onMouseEnter={() => setHover(`run-${e.id}`)}
                  onMouseLeave={() => setHover(null)}
                  sx={{
                    ...focusRing,
                    ...historyRowSx(false, connect),
                    cursor: seen ? "pointer" : "default",
                  }}
                >
                  <HistoryDot square />
                  <Box minWidth={0} flex={1}>
                    <Typography
                      sx={{ typography: "s3", color: "text.tertiary" }}
                    >
                      {whenLabel(e.r.finishedAt || e.r.startedAt)}
                    </Typography>
                    <Stack direction="row" alignItems="center" spacing={0.75}>
                      <Iconify
                        icon="solar:play-linear"
                        width={12}
                        sx={{ color: "text.secondary" }}
                      />
                      <Typography
                        noWrap
                        sx={{ typography: "s2", fontWeight: 600 }}
                      >
                        {`Run #${runNo.get(e.id) || e.no}${Number.isFinite(e.r.passRate) ? ` · ${Math.round(e.r.passRate)}% passed` : ""}`}
                      </Typography>
                    </Stack>
                    <Typography
                      noWrap
                      sx={{ typography: "s3", color: "text.tertiary" }}
                    >
                      {hover === `run-${e.id}` && seen
                        ? "Click to see the suite it ran on"
                        : `${n ? `${n} scenarios` : "All scenarios"}`}
                    </Typography>
                  </Box>
                </Stack>
              );
            }
            const m = e.m;
            const b = { meta: m, rows: m.scenarios };
            const n = m.scenarios.length;
            const blockers = m.scenarios.filter(
              (r) => r.critical && !r.proposed,
            ).length;
            /* Proposed in chat and not added yet: said so, not counted as the set. */
            const proposed = m.scenarios.filter((r) => r.proposed).length;
            const viewing = asOf === m.batchId;
            const later =
              asOf &&
              at(m.addedAt) >
                at(batches.find((x) => x.batchId === asOf)?.addedAt);
            return (
              <Stack
                key={e.id}
                direction="row"
                spacing={1.25}
                {...clickable(() => onPick(m.batchId), { pressed: viewing })}
                onMouseEnter={() => setHover(e.id)}
                onMouseLeave={() => setHover(null)}
                /* Keyboard focus reveals "View as of" the way hover does. */
                onFocus={() => setHover(e.id)}
                onBlur={(ev) => {
                  if (!ev.currentTarget.contains(ev.relatedTarget))
                    setHover(null);
                }}
                sx={{
                  ...focusRing,
                  ...historyRowSx(viewing, connect),
                  opacity: later ? 0.45 : 1,
                }}
              >
                <HistoryDot active={viewing} />
                <Box minWidth={0} flex={1}>
                  <Stack direction="row" alignItems="center" spacing={1}>
                    <Typography
                      sx={{ typography: "s2", fontWeight: 600, flex: 1 }}
                    >
                      {whenLabel(m.addedAt)}
                    </Typography>
                    {(hover === e.id || viewing) && (
                      <Typography
                        component="span"
                        {...clickable(
                          (ev) => {
                            ev.stopPropagation();
                            onViewAsOf(viewing ? null : m.batchId);
                          },
                          { role: "link" },
                        )}
                        sx={{
                          ...focusRing,
                          typography: "s3",
                          fontWeight: 600,
                          color: viewing ? "text.primary" : "text.secondary",
                          cursor: "pointer",
                          "&:hover": {
                            color: "text.primary",
                            textDecoration: "underline",
                          },
                        }}
                      >
                        {viewing ? "Viewing" : "View as of"}
                      </Typography>
                    )}
                  </Stack>
                  <Stack
                    direction="row"
                    alignItems="center"
                    spacing={0.75}
                    sx={{ mt: 0.375 }}
                  >
                    <Avatar batch={b} size={16} />
                    <Typography
                      noWrap
                      sx={{ typography: "s3", color: "text.secondary" }}
                    >
                      {who(b)}
                    </Typography>
                  </Stack>
                  <Typography
                    noWrap
                    sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
                  >
                    {m.source === "derived"
                      ? `Built the environment · ${n}`
                      : proposed === n
                        ? `${n} proposed ${HOW[m.source] || ""}`.trim()
                        : `+${n - proposed} ${HOW[m.source] || ""}`.trim()}
                    {blockers ? ` · ${blockers} must-pass` : ""}
                  </Typography>
                </Box>
              </Stack>
            );
          })}
        </Box>
      </Box>
    </Box>
  );
}
BatchHistoryPanel.propTypes = {
  batches: PropTypes.array,
  runs: PropTypes.array,
  versions: PropTypes.array,
  total: PropTypes.number,
  asOf: PropTypes.string,
  onPick: PropTypes.func,
  onViewAsOf: PropTypes.func,
  onClose: PropTypes.func,
};

/* "Showing the suite as of …" — says what is hidden and how to get back. */
export function AsOfBanner({ batch, shown, total, hiddenBatches, onBack }) {
  if (!batch) return null;
  return (
    <Alert
      severity="info"
      icon={<Iconify icon="solar:history-linear" width={16} />}
      sx={{ mb: 1.5 }}
      action={
        <Button
          size="small"
          variant="outlined"
          onClick={onBack}
          sx={{ flexShrink: 0 }}
        >
          Back to current
        </Button>
      }
    >
      <b>{`Showing the suite as of ${whenLabel(batch.addedAt)}`}</b>
      <Box component="span" sx={{ color: "text.tertiary" }}>
        {` · ${shown} of ${total} scenarios · ${hiddenBatches} later batch${hiddenBatches === 1 ? "" : "es"} hidden`}
      </Box>
    </Alert>
  );
}
AsOfBanner.propTypes = {
  batch: PropTypes.object,
  shown: PropTypes.number,
  total: PropTypes.number,
  hiddenBatches: PropTypes.number,
  onBack: PropTypes.func,
};
