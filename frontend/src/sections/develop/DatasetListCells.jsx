/* eslint-disable react/prop-types */
import React, { forwardRef, useState } from "react";
import {
  alpha,
  Box,
  Divider,
  IconButton,
  LinearProgress,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import { useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import {
  prefetchDatasetRuns,
  resolveExperimentHref,
} from "src/sections/common/Runs/experimentHref";
import DatasetModalityIcons, { ScenariosChip } from "./DatasetModalityIcons";
import CustomTooltip from "src/components/tooltip";
import { ACCESS_COPY } from "src/sections/common/Access/accessModel";
import { listDoor } from "./listAccessDoors";
import { tidyMenu } from "./datasetList";
import { useExperimentListActions } from "./experimentListActions";
import { VersionNumber } from "src/sections/common/Datasets/VersionChip";
import VersionTagChip from "src/sections/common/Datasets/VersionTagChip";
import DeltaBadge from "src/sections/common/Metrics/DeltaBadge";
import { getRunStatus } from "src/sections/common/Runs/runStatus";
import RelativeTime from "src/sections/common/ux/RelativeTime";
import { KeyHintTitle } from "src/sections/common/ux/KeyHintTooltip";
import { capForKey } from "src/utils/keymap";
import {
  fmtPts,
  fmtScore,
  fmtTimeAbsolute,
  fmtTimeRelative,
  MINUS,
} from "src/sections/common/format/fmt";
import useDatasetColumnsLite from "./useDatasetColumnsLite";
import {
  offeredOrganizeItems,
  ORGANIZE_READ_ONLY,
} from "./organize/organizeItems";
import {
  bestRunTooltip,
  datasetKindLine,
  highlightParts,
  runningTooltip,
  baselineMetric,
  metricLabel,
} from "./datasetList";
import { isSurfaceShown } from "src/launch/launchSwitch";

// Cells of the dataset list (PRODUCT-SPEC §3.1). Every value comes from the
// get-datasets item; missing values read "—" with a tooltip saying why.

const DASH = "—";

/** Parse the API's times: ISO with zone, or Django's "YYYY-MM-DD HH:MM" (UTC). */
export function parseApiTime(value) {
  if (!value) return null;
  const s = String(value);
  const iso = /[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : `${s.replace(" ", "T")}Z`;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

export const relativeTime = (value) => {
  const d = parseApiTime(value);
  if (!d) return DASH;
  return fmtTimeRelative(d.toISOString());
};

const absoluteTime = (value) => {
  const d = parseApiTime(value);
  return d ? fmtTimeAbsolute(d.toISOString()) : "";
};

/** Search hits in bold on a soft amber tint (dark-mode safe). DL#14/15. */
export const Highlight = ({ text, query }) =>
  highlightParts(text, query).map((p, i) =>
    p.match ? (
      <Box
        key={i}
        component="mark"
        sx={{
          bgcolor: (t) =>
            alpha(
              t.palette.warning.main,
              t.palette.mode === "dark" ? 0.35 : 0.25,
            ),
          color: "inherit",
          fontWeight: 700,
          borderRadius: "2px",
          px: "1px",
        }}
      >
        {p.text}
      </Box>
    ) : (
      <React.Fragment key={i}>{p.text}</React.Fragment>
    ),
  );

const Muted = ({ children, tooltip }) => (
  <CustomTooltip
    show={Boolean(tooltip)}
    title={tooltip ?? ""}
    size="small"
    arrow
  >
    <Typography typography="s2" color="text.disabled">
      {children}
    </Typography>
  </CustomTooltip>
);

const TagChip = ({ label, query }) => (
  <Box
    component="span"
    sx={{
      display: "inline-flex",
      alignItems: "center",
      height: 18,
      px: 0.75,
      borderRadius: 0.5,
      bgcolor: "action.hover",
      color: "text.secondary",
      typography: "s3",
      fontWeight: 500,
      flexShrink: 0,
      whiteSpace: "nowrap",
    }}
  >
    {query ? <Highlight text={label} query={query} /> : label}
  </Box>
);

const matches = (text, query) =>
  Boolean(query?.trim()) &&
  String(text ?? "")
    .toLowerCase()
    .includes(query.trim().toLowerCase());

/**
 * Name (600) first on line 1, never truncated at 1280 (it wraps to 2 lines
 * before it would clip; tags only take the room it leaves). Line 2 (IT-R1
 * G2 P7): modality icons + what the dataset tests, from its kind + counts
 * (`Task suite · 30 tasks with end-state checks`; same column query as
 * DatasetModalityIcons, so nothing refetches). The description is in the
 * line's tooltip; while the search matches only the description, line 2
 * shows it (highlighted) instead.
 */
// W9 A4 (integration H1): the per-browser ☆ "Pin to the top" is gone; the
// list's Name column wraps this cell with the per-user server star
// (DatasetStarToggle, datasetListColumns.jsx).
export const NameCell = ({ row, query }) => {
  // A scenarios dataset shows its kind chip; its `scenarios`
  // tag would repeat it, and the chip takes the inline tag's place.
  const kindChip = row.kind === "scenarios";
  const tags = (row.tags ?? []).filter((t) => !(kindChip && t === "scenarios"));
  // Tags never squeeze the name (DL#10): one tag, the rest `+n`.
  const shown = tags.slice(0, kindChip ? 0 : 1);
  const rest = tags.slice(shown.length);
  const { data: columns } = useDatasetColumnsLite(row.id);
  const line = datasetKindLine(row, columns);
  // `Multimodal · chat, image, audio…` already names the media.
  const icons = !line.startsWith("Multimodal");
  const descHit = matches(row.description, query) && !matches(row.name, query);
  return (
    <Stack
      sx={{ minWidth: 0, py: 0.5, width: "100%", position: "relative" }}
      gap={0.25}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        sx={{ minWidth: 0 }}
      >
        <Typography
          component={RouterLink}
          to={`/dashboard/develop/${row.id}?tab=data`}
          state={{ dataset: row }}
          // The row click opens it too; Cmd/middle-click opens a new tab.
          onClick={(ev) => {
            if (ev.metaKey || ev.ctrlKey || ev.shiftKey) ev.stopPropagation();
            else ev.preventDefault();
          }}
          typography="s1"
          fontWeight="fontWeightSemiBold"
          color="text.primary"
          title={row.name}
          sx={{
            flex: "0 0 auto",
            maxWidth: "100%",
            minWidth: 0,
            textDecoration: "none",
            whiteSpace: "normal",
            overflowWrap: "anywhere",
            lineHeight: 1.3,
            display: "-webkit-box",
            WebkitBoxOrient: "vertical",
            WebkitLineClamp: 2,
            overflow: "hidden",
            "&:hover": { textDecoration: "underline" },
          }}
        >
          <Highlight text={row.name} query={query} />
        </Typography>
        {/* W4 §9 (M6): a dataset restricted to named people shows a lock. */}
        {row.restricted ? (
          <CustomTooltip show size="small" title={ACCESS_COPY.restricted}>
            <Box
              component="span"
              aria-label="Restricted"
              data-testid="dataset-restricted-lock"
              sx={{ display: "inline-flex", flexShrink: 0 }}
            >
              <Iconify
                icon="mdi:lock-outline"
                width={14}
                sx={{ color: "text.secondary" }}
              />
            </Box>
          </CustomTooltip>
        ) : null}
        {/* Tags take what the name leaves: a chip that does not fit wraps
            out of this one-line box instead of squeezing the name. */}
        <Box
          sx={{
            flex: "1 1 0",
            minWidth: 0,
            height: 18,
            display: "flex",
            flexWrap: "wrap",
            alignItems: "center",
            gap: 0.75,
            overflow: "hidden",
          }}
        >
          {/* A 0-wide first item: even the first chip wraps out of view
              when it is too wide; a lone first item never wraps. */}
          <Box
            component="span"
            aria-hidden
            sx={{ width: 0, height: 18, mr: -0.75 }}
          />
          {kindChip ? <ScenariosChip /> : null}
          {shown.map((t) => (
            <TagChip key={t} label={t} query={query} />
          ))}
          {rest.length > 0 && (
            <CustomTooltip show title={rest.join(", ")} size="small" arrow>
              <Box
                component="span"
                aria-label={`More tags: ${rest.join(", ")}`}
              >
                <TagChip label={`+${rest.length}`} />
              </Box>
            </CustomTooltip>
          )}
        </Box>
      </Stack>
      <Box
        sx={{
          minWidth: 0,
          width: "100%",
          display: "flex",
          alignItems: "center",
          gap: 0.75,
        }}
      >
        {/* Modality icons lead the second line, so they
            never squeeze the name. */}
        {icons ? (
          <DatasetModalityIcons datasetId={row.id} kind={row.kind} />
        ) : null}
        <CustomTooltip
          show={Boolean(row.description)}
          title={row.description}
          size="small"
          enterDelay={400}
          placement="bottom-start"
        >
          <Typography
            component="div"
            typography="s3"
            color="text.secondary"
            noWrap
            data-testid="dataset-kind-line"
            data-kind-line={line}
            sx={{ display: "block", minWidth: 0, flex: 1 }}
          >
            {descHit ? (
              <Highlight text={row.description} query={query} />
            ) : (
              line
            )}
          </Typography>
        </CustomTooltip>
      </Box>
    </Stack>
  );
};

/** `v6` + the latest version's tag; tooltip: who, when, what. */
export const VersionCell = ({ row }) => {
  const v = row.latest_version;
  if (!v) return <Muted tooltip="No saved version yet">{DASH}</Muted>;
  const tag = v.tags?.[0];
  const when = v.created_at ? relativeTime(v.created_at) : null;
  const tooltip = [
    when || v.created_by_name
      ? `Saved ${when ?? ""}${v.created_by_name ? ` by ${v.created_by_name}` : ""}`
      : null,
    v.description ? `“${v.description}”` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <CustomTooltip show={Boolean(tooltip)} title={tooltip} size="small" arrow>
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        sx={{ minWidth: 0 }}
      >
        <VersionNumber number={v.number} />
        {tag && (
          <Box sx={{ display: "flex", minWidth: 0, overflow: "hidden" }}>
            <VersionTagChip name={tag} />
          </Box>
        )}
      </Stack>
    </CustomTooltip>
  );
};

/**
 * IT-R2 G2 P1: a link to an experiment (or to the experiment a run is in)
 * that lands on the Matrix when it holds 3+ runs, else on its Data tab.
 * The list payload does not say how many runs an experiment holds, so the
 * click resolves it from the dataset's runs (prefetched on hover); the href
 * stays the plain Data URL for a middle-click or a new tab.
 */
// A forwardRef: the Best cell wraps it in a CustomTooltip (MUI needs a ref).
const ExperimentLink = forwardRef(function ExperimentLink(
  { datasetId, experimentId, runId, fallback, children, sx, ...rest },
  ref,
) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  return (
    <Typography
      ref={ref}
      component={RouterLink}
      to={fallback}
      onMouseEnter={() => prefetchDatasetRuns(queryClient, datasetId)}
      onFocus={() => prefetchDatasetRuns(queryClient, datasetId)}
      onClick={async (ev) => {
        ev.stopPropagation();
        if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button !== 0) return;
        ev.preventDefault();
        const href = await resolveExperimentHref(queryClient, {
          datasetId,
          experimentId,
          runId,
          fallback,
        });
        navigate(href ?? fallback);
      }}
      sx={sx}
      {...rest}
    >
      {children}
    </Typography>
  );
});

/** `#7 Support reply v5 · temperature sweep`; line 2 `2h ago` or `Running · 21%`. */
export const LastExperimentCell = ({ row }) => {
  const e = row.last_experiment;
  if (!e)
    return <Muted tooltip="No experiments on this dataset yet">{DASH}</Muted>;
  const status = getRunStatus(e.status);
  const running = status.active;
  const pct = Math.round((e.progress ?? 0) * 100);
  return (
    <Stack sx={{ minWidth: 0, py: 0.75 }} gap={0.25}>
      {/* IT-R4 G2 P9: a cut-off name reads in full on hover. */}
      <CustomTooltip
        show
        title={`#${e.ordinal} ${e.name} · Opens the experiment`}
        size="small"
        arrow
        enterDelay={400}
      >
        <Box sx={{ minWidth: 0, display: "flex" }}>
          <ExperimentLink
            datasetId={row.id}
            experimentId={e.id}
            fallback={`/dashboard/develop/experiment/${e.id}/data?datasetId=${row.id}`}
            typography="s2"
            noWrap
            data-testid="last-experiment-link"
            sx={{
              color: "text.primary",
              textDecoration: "none",
              "&:hover": { color: "primary.main", textDecoration: "underline" },
            }}
          >
            <Box component="span" sx={{ fontWeight: 600, mr: 0.5 }}>
              #{e.ordinal}
            </Box>
            {e.name}
          </ExperimentLink>
        </Box>
      </CustomTooltip>
      {running ? (
        <CustomTooltip
          show
          title={runningTooltip(e, row.numberOfDatapoints)}
          size="small"
          arrow
        >
          <Stack direction="row" alignItems="center" gap={0.75}>
            <Typography
              typography="s3"
              sx={{
                color: (t) =>
                  t.palette.mode === "dark" ? status.darkColor : status.color,
                whiteSpace: "nowrap",
              }}
            >
              {status.label} · {pct}%
            </Typography>
            <LinearProgress
              variant="determinate"
              value={pct}
              aria-label={`Experiment ${e.ordinal} progress`}
              aria-valuetext={`${status.label}, ${pct}%`}
              sx={{ width: 56, height: 2, borderRadius: 1 }}
            />
          </Stack>
        </CustomTooltip>
      ) : (
        <CustomTooltip
          show
          title={absoluteTime(e.created_at)}
          size="small"
          arrow
        >
          <Typography typography="s3" color="text.secondary" noWrap>
            {status.key === "Completed"
              ? relativeTime(e.created_at)
              : `${status.label} · ${relativeTime(e.created_at)}`}
          </Typography>
        </CustomTooltip>
      )}
    </Stack>
  );
};

const metricDefOf = (row) => ({
  key: row.baseline?.metric_key,
  name: row.baseline?.metric_name,
  output_type: String(
    row.best_run?.display ?? row.baseline?.display ?? "",
  ).endsWith("%")
    ? "pass_fail"
    : "score",
  higher_is_better: row.best_run?.higher_is_better ?? true,
});

/** `#1A · 79.4%`: the pinned baseline run's primary metric. */
/**
 * `#2B · 65.6%` over the metric it is in (`Success rate`) — A1-01: the
 * retired `Score now` column lives on as this cell's second line and its
 * tooltip (`Pinned baseline #2B: 65.6% Success rate. Best so far: …`).
 */
export const BaselineCell = ({ row }) => {
  const b = row.baseline;
  if (!b)
    return (
      <Muted tooltip="No baseline pinned yet. Open the dataset and pin a run on the Experiments tab.">
        {DASH}
      </Muted>
    );
  const metric = baselineMetric(row);
  const best = row.best_run;
  // IT-R4 G2 P5: the baseline's trend vs its previous run (`· −1.1 vs #1B`).
  const prev = b.prev ?? null;
  const prevText = prev
    ? Math.abs(prev.delta_pts ?? 0) < 0.05
      ? `same as ${prev.label}`
      : `${prev.delta_pts > 0 ? "+" : MINUS}${Math.abs(prev.delta_pts).toFixed(1)} vs ${prev.label}`
    : null;
  const tip = `Pinned baseline ${b.label}: ${b.display}${metric ? ` ${metric}` : ""}.${prev ? ` ${prevText} (previous ${prev.kind === "run" ? "run" : "version"}) on the ${prev.n_shared} ${prev.noun ?? "row"}s both ran.` : ""}${best && best.run_id !== b.run_id ? ` Best so far: ${best.label} ${best.display}.` : ""} Open the dataset to compare runs.`;
  return (
    <CustomTooltip show title={tip} size="small" arrow>
      <Stack sx={{ minWidth: 0, py: 0.75 }} gap={0.25}>
        <Typography
          typography="s2"
          sx={{ fontVariantNumeric: "tabular-nums" }}
          noWrap
        >
          <Box component="span" sx={{ fontWeight: 600 }}>
            {b.label}
          </Box>
          <Box component="span" sx={{ color: "text.secondary" }}>
            {" · "}
          </Box>
          {b.display}
          {/* No change stays in the tooltip; the cell shows a real move. */}
          {prevText && !prevText.startsWith("same") ? (
            <Box component="span" sx={{ color: "text.secondary" }}>
              {` · ${prevText}`}
            </Box>
          ) : null}
        </Typography>
        {metric ? (
          <Typography typography="s3" color="text.secondary" noWrap>
            {metric}
          </Typography>
        ) : null}
      </Stack>
    </CustomTooltip>
  );
};

/** `#2B · 86.6%` + the delta vs the pinned baseline on shared rows. */
export const BestRunCell = ({ row }) => {
  const r = row.best_run;
  if (!r) return <Muted tooltip="No completed run with 30+ rows">{DASH}</Muted>;
  const def = metricDefOf(row);
  const metric = row.baseline?.metric_name ?? "score";
  const isBaseline = row.baseline?.run_id === r.run_id;
  const deltaText =
    r.delta == null
      ? null
      : String(r.display ?? "").endsWith("%")
        ? fmtPts(r.delta, { fraction: true })
        : `${r.delta > 0 ? "+" : r.delta < 0 ? MINUS : ""}${fmtScore(Math.abs(r.delta))}`;
  const tooltip = bestRunTooltip(row, deltaText);
  // IT-R5 G2 P8: the metric the score is in, under it (like Baseline).
  const metricName = metricLabel(r.metric_key ? r : row.baseline);
  void metric;
  return (
    <Stack sx={{ minWidth: 0, py: 0.75 }} gap={0.25}>
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        sx={{ minWidth: 0 }}
      >
        <CustomTooltip
          show
          title={`${tooltip} · Opens ${r.label}'s experiment.`}
          size="small"
          arrow
        >
          {/* IT-R2 G2 P1: the best run opens its own experiment (`#3E` →
            experiment 3), on the Matrix when it holds 3+ runs, with the
            page's verdict about that run (cand=). v2.1 g8 (list:view): the
            span takes the tooltip's hover and ref (the link alone did not
            pass them on, so the tooltip never showed). */}
          <Box
            component="span"
            data-testid="best-run-tip-target"
            sx={{ display: "inline-flex", minWidth: 0, flexShrink: 0 }}
          >
            <ExperimentLink
              datasetId={row.id}
              runId={r.run_id}
              fallback={`/dashboard/develop/${row.id}?tab=experiments`}
              typography="s2"
              noWrap
              data-testid="best-run-link"
              sx={{
                fontVariantNumeric: "tabular-nums",
                color: "text.primary",
                textDecoration: "none",
                // The score is never cut; the delta badge gives way first.
                flexShrink: 0,
                "&:hover": {
                  color: "primary.main",
                  textDecoration: "underline",
                },
              }}
            >
              <Box component="span" sx={{ fontWeight: 600 }}>
                {r.label}
              </Box>
              <Box component="span" sx={{ color: "text.secondary" }}>
                {" · "}
              </Box>
              {r.display}
            </ExperimentLink>
          </Box>
        </CustomTooltip>
        {!isBaseline && r.delta != null && (
          <DeltaBadge
            def={def}
            delta={r.delta}
            ci95={r.ci95}
            significant={r.significant}
            n={r.n_shared}
            vsLabel={row.baseline?.label}
          />
        )}
      </Stack>
      {metricName ? (
        <Typography
          typography="s3"
          color="text.secondary"
          noWrap
          data-testid="best-run-metric"
        >
          {/* RE-02: the delta's reference is not on this row, so name it. */}
          {!isBaseline && r.delta != null && row.baseline?.label
            ? `${metricName} · vs ${row.baseline.label}`
            : metricName}
        </Typography>
      ) : null}
    </Stack>
  );
};

/** Experiment count; opens the Experiments tab. */
export const ExperimentsCell = ({ row }) => {
  const n = row.numberOfExperiments ?? 0;
  if (!n)
    return (
      <Typography typography="s2" color="text.disabled">
        0
      </Typography>
    );
  return (
    <Typography
      component={RouterLink}
      to={`/dashboard/develop/${row.id}?tab=experiments`}
      onClick={(ev) => ev.stopPropagation()}
      typography="s2"
      sx={{
        color: "primary.main",
        textDecoration: "none",
        "&:hover": { textDecoration: "underline" },
      }}
    >
      {n}
    </Typography>
  );
};

export const TimeCell = ({ value, by }) => {
  const d = parseApiTime(value);
  return d ? (
    <Box
      sx={{ typography: "s2", color: "text.secondary", whiteSpace: "nowrap" }}
    >
      <RelativeTime iso={d.toISOString()} by={by ?? undefined} />
    </Box>
  ) : (
    <Muted tooltip="Not recorded">{DASH}</Muted>
  );
};

/** Owner (DL#9, hidden by default). */
export const OwnerCell = ({ row }) =>
  row.created_by_name ? (
    <Typography
      typography="s2"
      color="text.secondary"
      noWrap
      title={row.created_by_name}
    >
      {row.created_by_name}
    </Typography>
  ) : (
    <Muted tooltip="Created before owners were recorded">{DASH}</Muted>
  );

const SOURCE = {
  build: { label: "Build", icon: "mdi:database-outline" },
  demo: { label: "Sample", icon: "mdi:star-outline" },
  observe: { label: "Observe", icon: "mdi:transit-connection-variant" },
  sdk: { label: "SDK", icon: "mdi:code-braces" },
};
export const SourceCell = ({ value }) => {
  const s = SOURCE[value] ?? {
    label: value ?? DASH,
    icon: "mdi:database-outline",
  };
  return (
    <Stack direction="row" alignItems="center" gap={0.5}>
      <Iconify icon={s.icon} width={14} sx={{ color: "text.secondary" }} />
      <Typography typography="s2" color="text.secondary">
        {s.label}
      </Typography>
    </Stack>
  );
};

/**
 * Row actions (DL#18): one item list for the ⋮ menu and the right-click
 * menu. `actions`: {open, openExperiments, newExperiment, edit, duplicate,
 * copyId, copyLink, download, remove} and, when the host organises (W9
 * integration H1), `star(row)` and `organize(kind, row)`.
 */
const rowMenuItems = (props) => tidyMenu(allRowMenuItems(props));

const allRowMenuItems = ({ row, actions, canEdit, canDelete }) => {
  const run = listDoor(row, "run", canEdit, "Viewers can't start experiments");
  const edit = listDoor(row, "edit", canEdit, "Viewers can't edit datasets");
  const download = listDoor(row, "download");
  const remove = listDoor(
    row,
    "manage",
    canDelete,
    "Only owners and admins can delete datasets",
  );
  return [
    {
      label: "Open",
      icon: "mdi:open-in-app",
      run: actions.open,
      keys: ["Enter"],
    },
    // J09: keep both classic entry points without enabling DSX enhancements.
    ...(isSurfaceShown("exp.classic")
      ? [
          {
            label: "Open experiments",
            icon: "mdi:flask-outline",
            run: actions.openExperiments,
          },
        ]
      : []),
    ...(isSurfaceShown("exp.classic")
      ? [
          {
            label: "New experiment…",
            icon: "mdi:plus",
            run: actions.newExperiment,
            disabled: !run.ok,
            reason: run.reason,
          },
        ]
      : []),
    { divider: true, key: "d1" },
    {
      label: "Edit details…",
      icon: "mdi:pencil-outline",
      run: actions.edit,
      disabled: !edit.ok,
      reason: edit.reason,
    },
    // W9 A4 (H1): anyone who can see the dataset may star it (per person).
    ...(actions.star
      ? [
          {
            label: row?.starred ? "Unstar" : "Star",
            icon: row?.starred ? "mdi:star-off-outline" : "mdi:star-outline",
            run: actions.star,
          },
        ]
      : []),
    // W9 A1, A6, A7 (H1): the same dialogs as `Organize ▾` and ⌘K.
    ...(typeof actions.organize === "function"
      ? offeredOrganizeItems().map((item) => ({
          label: item.label,
          icon: item.icon,
          run: (r) => actions.organize(item.kind, r),
          disabled: !edit.ok,
          reason: canEdit ? edit.reason : ORGANIZE_READ_ONLY,
        }))
      : []),
    {
      label: "Duplicate",
      icon: "gg:duplicate",
      run: actions.duplicate,
      disabled: !canEdit,
      reason: "Viewers can't duplicate datasets",
      keys: ["shift+d"],
    },
    // MAIN-RULING-EXPORT-CONTRACT: CSV is an ordinary permission-checked
    // action. Only the dialog's advanced formats need dataset.export.
    {
      label: "Download…",
      icon: "mdi:download",
      run: actions.download,
      disabled: !download.ok,
      reason: download.reason,
    },
    { label: "Copy link", icon: "mdi:link-variant", run: actions.copyLink },
    { label: "Copy dataset id", icon: "mdi:content-copy", run: actions.copyId },
    { divider: true, key: "d2" },
    {
      label: "Delete…",
      icon: "solar:trash-bin-trash-linear",
      run: actions.remove,
      danger: true,
      disabled: !remove.ok,
      reason: remove.reason,
    },
  ];
};

/** The menu body; shared by the ⋮ button and the row's right-click. */
export const RowMenuList = ({
  row,
  actions,
  canEdit,
  canDelete,
  onClose,
  ...menuProps
}) => (
  <Menu
    onClose={(e) => {
      e?.stopPropagation?.();
      onClose();
    }}
    onClick={(e) => e.stopPropagation()}
    PaperProps={{ sx: { width: 236 } }}
    {...menuProps}
  >
    {rowMenuItems({
      row,
      actions: useExperimentListActions(actions),
      canEdit,
      canDelete,
    }).map((it) =>
      it.divider ? (
        <Divider key={it.key} sx={{ my: 0.5 }} />
      ) : (
        <CustomTooltip
          key={it.label}
          show={Boolean(it.disabled && it.reason)}
          title={it.reason ?? ""}
          size="small"
          placement="left"
        >
          <span>
            <MenuItem
              disabled={it.disabled}
              onClick={(e) => {
                e.stopPropagation();
                onClose();
                it.run?.(row);
              }}
              sx={{
                typography: "s2",
                py: 0.75,
                color: it.danger ? "error.main" : undefined,
              }}
            >
              <ListItemIcon
                sx={{
                  minWidth: 28,
                  color: it.danger ? "error.main" : undefined,
                }}
              >
                <Iconify icon={it.icon} width={16} />
              </ListItemIcon>
              <ListItemText
                primary={it.label}
                primaryTypographyProps={{ typography: "s2" }}
              />
              {it.keys && (
                <Typography
                  typography="s3"
                  color="text.disabled"
                  sx={{ ml: 1 }}
                >
                  {it.keys.map(capForKey).join(" ")}
                </Typography>
              )}
            </MenuItem>
          </span>
        </CustomTooltip>
      ),
    )}
  </Menu>
);

/** Row ⋮ button + menu. */
export const RowMenu = ({ row, actions, canEdit, canDelete }) => {
  const [anchor, setAnchor] = useState(null);
  return (
    <>
      <CustomTooltip
        show
        size="small"
        title={<KeyHintTitle title="Actions · or right-click the row" />}
      >
        <IconButton
          size="small"
          aria-label={`${row.name} actions`}
          // The grid focuses (and re-renders) the cell on mousedown, which
          // would swallow the click.
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.stopPropagation();
            setAnchor(e.currentTarget);
          }}
        >
          <Iconify icon="mdi:dots-vertical" width={18} />
        </IconButton>
      </CustomTooltip>
      <RowMenuList
        row={row}
        actions={actions}
        canEdit={canEdit}
        canDelete={canDelete}
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      />
    </>
  );
};
