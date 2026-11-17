import React, { useLayoutEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Divider,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Skeleton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import CopyId from "src/sections/common/ux/CopyId";
import Term from "src/sections/common/ux/Term";
import KeyHintTooltip from "src/sections/common/ux/KeyHintTooltip";
import { HoverCard } from "src/sections/common/ux/oneview";
import { NUM_SX } from "src/sections/common/format/fmt";
import SourceLogo from "src/sections/common/RunSources/SourceLogo";
import { entryOfConfig } from "src/sections/common/RunSources/catalog";
import {
  captureLabel,
  provenanceLine,
  provenanceOf,
  recordedMachineText,
} from "src/sections/common/Runs/provenanceModel";
import { runModelsText } from "src/sections/common/Runs/runLabel";
import TrialPips from "./TrialPips";
import {
  kindChip,
  metricDisplay,
  nextFmt,
  modelText,
  trialMetrics,
  trialsSummary,
  pipTooltip,
} from "./workspaceModel";

export const LANE_HEADER_H = 56;

const VERDICT_ICON = {
  pass: { icon: "mdi:check-circle", color: "success.main" },
  fail: { icon: "mdi:close-circle", color: "error.main" },
  flaky: { icon: "mdi:circle-half-full", color: "warning.main" },
  error: { icon: "mdi:alert-circle", color: "error.main" },
  none: { icon: "mdi:minus-circle-outline", color: "text.secondary" },
};

/** `Passed 3/3` chip: icon + text (never colour alone). */
export function VerdictChip({ verdict, size = "small", compact = false }) {
  const v = VERDICT_ICON[verdict?.tone] ?? VERDICT_ICON.none;
  const full = verdict?.label ?? "—";
  // IT-R2 G4 P9: a narrow lane keeps the count (`✗ 0/3`); the word is in
  // the icon and the tooltip, so the run name never shrinks to `Supp…`.
  const label =
    compact && /\d+\/\d+$/.test(full) ? full.match(/\d+\/\d+$/)[0] : full;
  const flaky = verdict?.key === "flaky";
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.5}
      sx={{
        flexShrink: 0,
        px: 0.75,
        height: size === "small" ? 22 : 26,
        borderRadius: "11px",
        bgcolor: "action.hover",
      }}
    >
      <Iconify icon={v.icon} width={14} sx={{ color: v.color }} />
      <Typography
        typography="s2"
        fontWeight={600}
        sx={{ whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}
      >
        {flaky && label !== full ? (
          label
        ) : flaky ? (
          <>
            <Term k="flaky">Flaky</Term>
            {label.slice(5)}
          </>
        ) : (
          label
        )}
      </Typography>
    </Stack>
  );
}
VerdictChip.propTypes = {
  verdict: PropTypes.object,
  size: PropTypes.string,
  compact: PropTypes.bool,
};

const METRICS_BASIC = ["steps", "cost", "wall"];
const METRICS_ALL = [
  "steps",
  "toolErrors",
  "cost",
  "wall",
  "tokens",
  "sandbox",
];

const METRIC_LABEL = {
  steps: "Steps",
  toolErrors: "Tool errors",
  cost: "Cost",
  wall: "Time",
  tokens: "Tokens",
  sandbox: "Sandbox time",
};
const DELTA_GO = {
  steps: "See the steps that differ",
  toolErrors: "Go to the first tool error",
  cost: "Open the timeline",
  wall: "Open the timeline",
  tokens: "Open the timeline",
  sandbox: "Open the timeline",
};
const FMTS = [
  ["abs", "Values"],
  ["delta", "Δ vs baseline"],
  ["ratio", "× baseline"],
];

const metricItems = (keys, metrics, base, fmt) =>
  keys
    .filter((k) => k !== "sandbox" || metrics.sandbox != null)
    .filter((k) => k !== "toolErrors" || metrics.toolErrors)
    .map((k) =>
      k === "toolErrors"
        ? {
            k,
            text: `${metrics.toolErrors} tool ${metrics.toolErrors === 1 ? "error" : "errors"}`,
            abs: `${metrics.toolErrors}`,
            delta:
              base && base.toolErrors !== metrics.toolErrors
                ? `${metrics.toolErrors - (base.toolErrors ?? 0) > 0 ? "+" : "−"}${Math.abs(metrics.toolErrors - (base.toolErrors ?? 0))}`
                : null,
          }
        : {
            k,
            ...metricDisplay(
              k,
              metrics[k],
              base ? base[k] : null,
              base ? fmt : "abs",
            ),
          },
    );

/**
 * IT-R5 G4 P4: the run info at the top of the lane's `⋯` menu: the full
 * model, framework or harness with its version, the agent or prompt
 * version, tools by name and MCP servers (from provenanceOf; never a
 * second model).
 */
export function runInfoRows(prov, modelName) {
  if (!prov) return [];
  const models = (prov.models ?? []).map((m) => m.id).filter(Boolean);
  const fw = prov.harness?.name
    ? [prov.harness.name, prov.harness.version].filter(Boolean).join(" ")
    : prov.source?.name ?? null;
  const ver = prov.target?.name
    ? [prov.target.name, prov.target.version].filter(Boolean).join(" ")
    : null;
  const tools = (prov.tools ?? []).map((t) => t.name).filter(Boolean);
  const mcp = (prov.mcp ?? []).map((m) =>
    m.tools ? `${m.name} (${m.tools} tools)` : m.name,
  );
  return [
    ["Model", models.length ? models.join(", ") : modelName],
    ["Framework", fw],
    ["Version", ver],
    ["Tools", tools.length ? tools.join(", ") : null],
    ["MCP", mcp.length ? mcp.join(", ") : null],
  ].filter(([, v]) => v);
}

function RunInfoRows({ prov, modelName }) {
  const rows = runInfoRows(prov, modelName);
  if (!rows.length) return null;
  return (
    <Box
      component="dl"
      data-testid="lane-run-info"
      sx={{
        m: 0,
        mt: 0.75,
        display: "grid",
        gridTemplateColumns: "auto 1fr",
        columnGap: 1.25,
        rowGap: 0.25,
        maxWidth: 340,
      }}
    >
      {rows.map(([k, v]) => (
        <React.Fragment key={k}>
          <Typography component="dt" typography="s3" color="text.secondary">
            {k}
          </Typography>
          <Typography
            component="dd"
            typography="s3"
            sx={{ m: 0, overflowWrap: "anywhere" }}
          >
            {v}
          </Typography>
        </React.Fragment>
      ))}
    </Box>
  );
}
RunInfoRows.propTypes = { prov: PropTypes.object, modelName: PropTypes.string };

/** The `+N` card: every number, each delta vs the baseline as a link. */
function MetricsCard({
  metrics,
  base,
  fmt,
  onFmt,
  onDelta,
  kind,
  count,
  names = [],
}) {
  const rows = metricItems(METRICS_ALL, metrics, base, "abs");
  // IT-R5 G4 P4: `More: tokens, sandbox` (never a bare `+2`).
  const more = names.length
    ? `More: ${names.map((n) => n.toLowerCase().replace(/ time$/, "")).join(", ")}`
    : `+${count}`;
  return (
    <HoverCard
      title="This trial"
      maxWidth={300}
      trigger={
        <Typography
          component="span"
          typography="s3"
          data-testid="lane-more-metrics"
          sx={{
            color: "text.secondary",
            whiteSpace: "nowrap",
            textDecoration: "underline dotted",
            textUnderlineOffset: 3,
          }}
        >
          {more}
        </Typography>
      }
      triggerProps={{ "aria-label": `${count} more numbers for this trial` }}
    >
      <Stack spacing={0.5} sx={{ minWidth: 220 }}>
        {kind ? (
          <Typography typography="s3" color="text.secondary">
            {kind}
          </Typography>
        ) : null}
        {rows.map((r) => (
          <Stack key={r.k} direction="row" alignItems="baseline" spacing={1}>
            <Typography typography="s2" color="text.secondary" sx={{ flex: 1 }}>
              {METRIC_LABEL[r.k]}
            </Typography>
            <Typography typography="s2" sx={{ ...NUM_SX }}>
              {r.k === "toolErrors" ? r.abs : r.abs ?? r.text}
            </Typography>
            {base ? (
              r.delta ? (
                <CustomTooltip
                  describeChild
                  show
                  size="small"
                  title={DELTA_GO[r.k]}
                >
                  <Box
                    component="button"
                    type="button"
                    onClick={() => onDelta?.(r.k)}
                    sx={{
                      all: "unset",
                      ...NUM_SX,
                      typography: "s3",
                      minWidth: 64,
                      textAlign: "right",
                      color: "primary.main",
                      cursor: "pointer",
                      "&:hover": { textDecoration: "underline" },
                      "&:focus-visible": { outline: "2px solid" },
                    }}
                  >
                    {r.delta}
                  </Box>
                </CustomTooltip>
              ) : (
                <Box sx={{ minWidth: 64 }} />
              )
            ) : null}
          </Stack>
        ))}
        {base ? (
          <Stack direction="row" spacing={0.5} sx={{ pt: 0.5 }}>
            <Typography typography="s3" color="text.secondary" sx={{ mr: 0.5 }}>
              Show as
            </Typography>
            {FMTS.map(([v, label]) => (
              <Box
                key={v}
                component="button"
                type="button"
                aria-pressed={fmt === v}
                onClick={() => onFmt?.(v)}
                sx={{
                  all: "unset",
                  typography: "s3",
                  cursor: "pointer",
                  px: 0.5,
                  borderRadius: 0.5,
                  fontWeight: fmt === v ? 700 : 400,
                  bgcolor: fmt === v ? "action.selected" : "transparent",
                  "&:focus-visible": { outline: "2px solid" },
                }}
              >
                {label}
              </Box>
            ))}
          </Stack>
        ) : (
          <Typography typography="s3" color="text.secondary">
            This is the baseline: other lanes show their difference to it.
          </Typography>
        )}
      </Stack>
    </HoverCard>
  );
}
MetricsCard.propTypes = {
  metrics: PropTypes.object.isRequired,
  base: PropTypes.object,
  fmt: PropTypes.string,
  onFmt: PropTypes.func,
  onDelta: PropTypes.func,
  kind: PropTypes.string,
  count: PropTypes.number,
  names: PropTypes.arrayOf(PropTypes.string),
};

/**
 * One line of trial numbers (`5 steps · $0.0037 · 6.4 s`). Numbers that
 * don't fit the lane drop whole and join the `+N` card, so a number is
 * never cut in the middle. `Lane metrics: All` (View) shows every number.
 */
function MetricsLine({ metrics, base, fmt, onFmt, onDelta, all, blind, kind }) {
  const boxRef = useRef(null);
  const [hidden, setHidden] = useState([]);
  const items = metricItems(
    all ? METRICS_ALL : METRICS_BASIC,
    metrics,
    base,
    fmt,
  );
  const extraItems = metricItems(METRICS_ALL, metrics, base, fmt).filter(
    (it) => !items.some((x) => x.k === it.k),
  );
  const extra = extraItems.length;
  const sig = items.map((it) => it.text).join("|");
  useLayoutEffect(() => {
    const el = boxRef.current;
    if (!el) return undefined;
    const measure = () => {
      const kids = [...el.children];
      const top = kids[0]?.offsetTop ?? 0;
      const next = kids
        .filter((c) => c.offsetTop > top + 2)
        .map((c) => c.dataset.metric);
      setHidden((prev) => (prev.join() === next.join() ? prev : next));
    };
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [sig, blind]);
  if (blind)
    return (
      <Typography typography="s3" color="text.disabled" noWrap>
        Numbers hidden in blind review
      </Typography>
    );
  const count = hidden.length + extra;
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.5}
      sx={{ minWidth: 0, flex: "1 1 auto" }}
    >
      <Box
        ref={boxRef}
        sx={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          columnGap: 0.75,
          minWidth: 0,
          height: 20,
          overflow: "hidden",
          whiteSpace: "nowrap",
        }}
      >
        {items.map(({ k, text, delta }, i) => (
          <CustomTooltip
            describeChild
            key={k}
            show
            size="small"
            title={`${METRIC_LABEL[k]} of this trial${base && delta ? ` · ${delta} vs baseline` : ""}`}
          >
            <Typography
              component="span"
              data-metric={k}
              typography="s2"
              sx={{
                ...NUM_SX,
                textAlign: "left",
                flexShrink: 0,
                color: k === "toolErrors" ? "error.main" : "text.primary",
              }}
            >
              {i ? "· " : ""}
              {text}
            </Typography>
          </CustomTooltip>
        ))}
      </Box>
      {count ? (
        <MetricsCard
          metrics={metrics}
          base={base}
          fmt={fmt}
          onFmt={onFmt}
          onDelta={onDelta}
          kind={kind}
          count={count}
          names={[...hidden, ...extraItems.map((it) => it.k)]
            .map((k) => METRIC_LABEL[k])
            .filter(Boolean)}
        />
      ) : null}
    </Stack>
  );
}
MetricsLine.propTypes = {
  metrics: PropTypes.object.isRequired,
  base: PropTypes.object,
  fmt: PropTypes.string,
  onFmt: PropTypes.func,
  onDelta: PropTypes.func,
  all: PropTypes.bool,
  blind: PropTypes.bool,
  kind: PropTypes.string,
};

/**
 * One lane header (ONE-VIEW-SPEC §7.1 item 5, A4-08), 56 px: run-colour
 * bar; name · `Baseline` · status `Passed 3/3` · `⋯`; trial pips with
 * `trial 2 of 3 ‹ ›` (`[` `]`) and `5 steps · $0.0037 · 6.4 s` + `+3` (a
 * card with tool errors, tokens, sandbox and every delta vs the baseline as
 * a link to its steps). Right-click = the same menu as `⋯`. The kind shows
 * inline when the lane has room, else in the name's tooltip and the card.
 */
export default function LaneHeader({
  lane,
  trial,
  baseTrial,
  isBaseline,
  focused,
  onFocus,
  onTrial,
  onStepTrial,
  fmt,
  onFmt,
  onDelta,
  blind,
  actions,
  loading,
  allTrials,
  metricsAll,
  displayName,
  run,
  onTestFix,
}) {
  const [menu, setMenu] = useState(null);
  const rootRef = useRef(null);
  // Room decides labels only (never features): `trial 2 of 3` from 320 px,
  // the kind chip from 420 px (else in the name's tooltip, the +N card and
  // the ⋯ menu).
  const [room, setRoom] = useState(0);
  useLayoutEffect(() => {
    const el = rootRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(([e]) =>
      setRoom(Math.round(e.contentRect.width)),
    );
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  if (loading || !lane)
    return (
      <Box
        sx={{
          height: LANE_HEADER_H,
          p: 1.25,
          borderTop: "3px solid",
          borderColor: "divider",
        }}
      >
        <Skeleton width="60%" height={20} />
        <Skeleton width="80%" height={18} />
      </Box>
    );
  const color = lane.color?.solid;
  const metrics = trialMetrics(trial);
  const base = isBaseline || !baseTrial ? null : trialMetrics(baseTrial);
  const name = displayName ?? lane.blindName ?? lane.label;
  const summary = trialsSummary(lane);
  const nTrials = lane.trials?.length ?? 0;
  const prod = lane.kind === "production";
  const noTrace = lane.ran_row === false || lane.noTrace;
  const kind = lane.blindName ? null : kindChip(lane.kind);
  // What the run is (SourceLogo, provenance line), how it was
  // captured (harness lanes) and where it ran (ConnectionPill, local runs).
  const prov = run && !lane.blindName ? provenanceOf(run) : null;
  const entry =
    run && !lane.blindName
      ? entryOfConfig({ ...(run.config ?? {}), kind: lane.kind })
      : null;
  const provLine = prov ? provenanceLine(prov) : "";
  const capture = prov && lane.kind === "harness" ? captureLabel(prov) : null;
  const localMachine =
    prov?.where?.mode === "local" ? prov.where.machineId : null;
  const failedLane = ["fail", "flaky", "error"].includes(lane.verdict?.key);
  // IT-R2 G4 P9: the model on the second line (`Claude Code` /
  // `claude-sonnet-4-5`), from the run name or its config.
  // IT-R3 G4 P1: always text — code and harness configs carry `{id,
  // params}` (rendering that object crashed the whole page).
  const modelName = lane.blindName
    ? null
    : String(lane.name ?? "").split(/\s+×\s+/)[1] ||
      runModelsText(run) ||
      modelText(run?.config?.model) ||
      modelText(run?.config?.model_name) ||
      null;
  const fullTip = [
    lane.blindName ? null : lane.name,
    lane.blindName ? null : `${kind} · ${lane.label}`,
    provLine || null,
    capture,
    summary,
    lane.blindName || prod ? null : "⋯ shows the model, tools and MCP",
  ]
    .filter(Boolean)
    .join(" · ");
  const openMenu = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setMenu(
      e.type === "contextmenu"
        ? { pos: { top: e.clientY, left: e.clientX } }
        : { el: e.currentTarget },
    );
  };
  const arrow = (dir) => (
    <KeyHintTooltip
      title={dir < 0 ? "Previous trial" : "Next trial"}
      action={dir < 0 ? "prevTrial" : "nextTrial"}
    >
      <IconButton
        size="small"
        aria-label={
          dir < 0 ? `Previous trial of ${name}` : `Next trial of ${name}`
        }
        onClick={(e) => (e.stopPropagation(), onStepTrial?.(dir))}
        sx={{ p: 0, width: 18, height: 18, color: "text.secondary" }}
      >
        <Iconify
          icon={dir < 0 ? "mdi:chevron-left" : "mdi:chevron-right"}
          width={16}
        />
      </IconButton>
    </KeyHintTooltip>
  );
  return (
    <Box
      ref={rootRef}
      onClick={onFocus}
      onContextMenu={openMenu}
      data-lane-header
      sx={{
        px: 1.25,
        pt: 0.25,
        pb: 0.25,
        borderTop: "3px solid",
        borderTopColor: color,
        boxSizing: "border-box",
        bgcolor: "background.paper",
        cursor: "default",
        // Trial arrows show on hover / focus (the pips say which trial;
        // `[` `]` always work), so a resting header stays calm.
        "& .tc-trial-nav": { opacity: 0, transition: "opacity 120ms" },
        "&:hover .tc-trial-nav, &:focus-within .tc-trial-nav": { opacity: 1 },
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{ minWidth: 0, height: 24 }}
      >
        {entry ? (
          <Box component="span" sx={{ display: "inline-flex", flexShrink: 0 }}>
            <SourceLogo entry={entry} size={16} tooltip={provLine || true} />
          </Box>
        ) : null}
        <CustomTooltip describeChild show size="small" title={fullTip}>
          <Typography
            typography="s1"
            fontWeight={700}
            sx={{
              minWidth: 56,
              // IT-R1 G4: the run name shrinks last (the model, kind and
              // capture chips give way first); the full name is in the
              // tooltip. IT-R2 G4 P9: it never shrinks while those can.
              flex: "0 0 auto",
              maxWidth: "62%",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {(() => {
              // IT-R5 G4 P4: `#2F Support Ag… v3.1 · all rows`: the run id
              // first, the differentiator after ` · ` always visible, the
              // agent name truncated in between.
              const cut = String(name ?? "").lastIndexOf(" · ");
              const head = cut > 0 ? name.slice(0, cut) : name;
              const tail = cut > 0 ? name.slice(cut) : "";
              const id =
                !lane.blindName &&
                lane.label &&
                !String(name).includes(lane.label)
                  ? lane.label
                  : null;
              return (
                <Box
                  component="span"
                  sx={{ display: "flex", minWidth: 0, alignItems: "baseline" }}
                >
                  {id ? (
                    <Box
                      component="span"
                      data-testid="lane-run-id"
                      sx={{ flexShrink: 0, mr: 0.5, color: "text.secondary" }}
                    >
                      {id}
                    </Box>
                  ) : null}
                  <Box
                    component="span"
                    sx={{
                      minWidth: 0,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {head}
                  </Box>
                  {tail ? (
                    <Box
                      component="span"
                      sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
                    >
                      {tail}
                    </Box>
                  ) : null}
                </Box>
              );
            })()}
          </Typography>
        </CustomTooltip>
        {modelName ? (
          // IT-R2 G4 P9: the model beside the name (`Claude Code
          // claude-sonnet-4-5`); it gives way before the name does.
          <Typography
            typography="s3"
            color="text.secondary"
            data-testid="lane-model"
            title={modelName}
            sx={{
              flex: "0 1 auto",
              minWidth: 0,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              ml: "4px !important",
            }}
          >
            {modelName}
          </Typography>
        ) : null}
        {isBaseline && !lane.blindName && room < 380 ? (
          <CustomTooltip describeChild show size="small" title="Baseline">
            <Iconify
              icon="mdi:pin-outline"
              width={14}
              aria-label="Baseline"
              sx={{ color: "text.secondary", flexShrink: 0 }}
            />
          </CustomTooltip>
        ) : null}
        {isBaseline && !lane.blindName && room >= 380 ? (
          <Box
            component="span"
            sx={{
              flexShrink: 0,
              px: 0.5,
              height: 18,
              lineHeight: "16px",
              borderRadius: "2px",
              typography: "s3",
              fontWeight: 600,
              border: "1px solid",
              borderColor: color,
              color: "text.primary",
              whiteSpace: "nowrap",
            }}
          >
            <Term k="baseline">Baseline</Term>
          </Box>
        ) : null}
        {kind && room >= 560 ? (
          <Box
            component="span"
            sx={{
              flexShrink: 1,
              minWidth: 24,
              overflow: "hidden",
              textOverflow: "ellipsis",
              px: 0.5,
              height: 18,
              lineHeight: "18px",
              borderRadius: "2px",
              typography: "s3",
              bgcolor: "action.hover",
              color: "text.secondary",
              whiteSpace: "nowrap",
            }}
          >
            {kind}
          </Box>
        ) : null}
        {capture && room >= 620 ? (
          <CustomTooltip describeChild show size="small" title={capture}>
            <Box
              component="span"
              data-capture-chip
              sx={{
                flexShrink: 1,
                minWidth: 24,
                overflow: "hidden",
                textOverflow: "ellipsis",
                px: 0.5,
                height: 18,
                lineHeight: "18px",
                borderRadius: "2px",
                typography: "s3",
                border: "1px solid",
                borderColor: "divider",
                color: "text.secondary",
                whiteSpace: "nowrap",
              }}
            >
              {capture.split(" (")[0]}
            </Box>
          </CustomTooltip>
        ) : null}
        {/* options-truth L7b G4 (connect:pill): where the lane's run ran,
            as recorded (connecting a machine is absent): never a live
            online / offline pill. */}
        {localMachine && room >= 520 ? (
          <Typography
            typography="s3"
            color="text.secondary"
            noWrap
            data-testid="lane-ran-on"
            sx={{ flexShrink: 1, minWidth: 0 }}
          >
            {recordedMachineText(prov)}
          </Typography>
        ) : null}
        <Box sx={{ flex: 1 }} />
        {failedLane && onTestFix ? (
          <CustomTooltip
            describeChild
            show
            size="small"
            title={`Test a fix for ${name} on this task's failed tries`}
          >
            <Button
              size="small"
              color="inherit"
              onClick={(e) => (e.stopPropagation(), onTestFix())}
              startIcon={
                room >= 460 ? (
                  <Iconify icon="mdi:wrench-cog-outline" width={14} />
                ) : null
              }
              aria-label={`Test a fix for ${name}`}
              sx={{
                flexShrink: 0,
                typography: "s3",
                fontWeight: 600,
                px: 0.75,
                py: 0,
                minWidth: 0,
                height: 22,
                border: "1px solid",
                borderColor: "divider",
                whiteSpace: "nowrap",
              }}
              data-testid="lane-test-fix"
            >
              {room >= 460 ? (
                "Test a fix"
              ) : (
                <Iconify icon="mdi:wrench-cog-outline" width={14} />
              )}
            </Button>
          </CustomTooltip>
        ) : null}
        {room >= 380 || !room ? (
          <CustomTooltip
            describeChild
            show={room < 420}
            size="small"
            title={lane.verdict?.label ?? ""}
          >
            <span style={{ display: "inline-flex", flexShrink: 0 }}>
              <VerdictChip verdict={lane.verdict} compact={room < 420} />
            </span>
          </CustomTooltip>
        ) : null}
        <CustomTooltip describeChild show size="small" title="Run actions">
          <IconButton
            size="small"
            onClick={openMenu}
            aria-label={`Actions for ${name}`}
            aria-haspopup="menu"
            sx={{ flexShrink: 0, p: 0.25 }}
          >
            <Iconify icon="mdi:dots-horizontal" width={18} />
          </IconButton>
        </CustomTooltip>
      </Stack>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{ mt: 0.25, minWidth: 0, height: 22 }}
      >
        {prod ? (
          <Typography typography="s3" color="text.secondary" noWrap>
            Production trace · no checks ·{" "}
            {lane.session?.resolution
              ? `resolution ${lane.session.resolution}`
              : "not scored"}
          </Typography>
        ) : (
          <>
            {room && room < 380 ? (
              // IT-R5 G4 P4: a narrow lane puts its result on this row, so
              // the title keeps its room.
              <span style={{ display: "inline-flex", flexShrink: 0 }}>
                <VerdictChip verdict={lane.verdict} compact />
              </span>
            ) : null}
            {nTrials ? (
              <CustomTooltip
                describeChild
                show
                size="small"
                title={`${summary || "Trials of this run on this task"}${nTrials > 1 ? ` · ‹ › or ${focused ? "" : "click the lane, then "}[ ] to switch` : ""}`}
              >
                <Box sx={{ display: "flex", flexShrink: 0 }}>
                  <TrialPips
                    trials={lane.trials}
                    selected={trial?.trial}
                    onSelect={nTrials > 1 ? onTrial : undefined}
                    ringColor={color}
                  />
                </Box>
              </CustomTooltip>
            ) : null}
            {nTrials > 1 ? (
              <Stack direction="row" alignItems="center" sx={{ flexShrink: 0 }}>
                <Typography
                  typography="s3"
                  color="text.secondary"
                  sx={{
                    whiteSpace: "nowrap",
                    fontVariantNumeric: "tabular-nums",
                    mr: 0.25,
                  }}
                >
                  {room >= 320 ? (
                    <>
                      <Term k="trial">trial</Term> {trial?.trial ?? "—"} of{" "}
                      {nTrials}
                    </>
                  ) : (
                    `${trial?.trial ?? "—"}/${nTrials}`
                  )}
                </Typography>
                <Box className="tc-trial-nav" sx={{ display: "flex" }}>
                  {arrow(-1)}
                  {arrow(1)}
                </Box>
              </Stack>
            ) : null}
            <Box
              sx={{
                width: "1px",
                height: 14,
                bgcolor: "divider",
                flexShrink: 0,
              }}
            />
            {noTrace ? (
              <CustomTooltip
                describeChild
                show
                size="small"
                title={lane.noTraceReason ?? lane.not_run_reason ?? ""}
              >
                <Typography typography="s3" color="text.secondary" noWrap>
                  {lane.ran_row === false
                    ? lane.not_run_reason
                    : `No trace · ${lane.noTraceReason}`}
                </Typography>
              </CustomTooltip>
            ) : (
              <MetricsLine
                metrics={metrics}
                base={base}
                fmt={fmt}
                onFmt={onFmt}
                onDelta={onDelta}
                all={metricsAll}
                blind={blind}
                kind={kind}
              />
            )}
          </>
        )}
      </Stack>
      {allTrials ? (
        <Stack
          spacing="3px"
          sx={{ mt: 0.5, mb: 0.25 }}
          aria-label="Every trial of this run"
        >
          {(lane.trials ?? []).map((t) => (
            <CustomTooltip
              describeChild
              key={t.trial}
              show
              size="small"
              title={pipTooltip(t)}
            >
              <Stack
                direction="row"
                alignItems="center"
                spacing={0.5}
                onClick={(e) => (e.stopPropagation(), onTrial(t.trial))}
                sx={{
                  cursor: "pointer",
                  opacity: t.trial === trial?.trial ? 1 : 0.55,
                }}
              >
                <Typography
                  typography="s3"
                  sx={{
                    fontSize: 10,
                    width: 14,
                    color: "text.secondary",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {t.trial}
                </Typography>
                <Stack direction="row" sx={{ flex: 1, gap: "1px", height: 6 }}>
                  {(t.steps ?? [])
                    .filter((s) => s.type !== "end_state")
                    .map((s) => (
                      <Box
                        key={s.n}
                        sx={{
                          flex: 1,
                          minWidth: 2,
                          borderRadius: "1px",
                          bgcolor:
                            s.status === "error"
                              ? "error.main"
                              : s.type === "customer"
                                ? "text.disabled"
                                : s.role === "assistant"
                                  ? "info.main"
                                  : s.type === "tool_call" ||
                                      s.node_type === "tool" ||
                                      s.node_type === "retriever"
                                    ? "success.main"
                                    : "action.disabled",
                        }}
                      />
                    ))}
                  {!(t.steps ?? []).length ? (
                    <Box
                      sx={{
                        flex: 1,
                        borderTop: "1px dashed",
                        borderColor: "divider",
                        mt: "3px",
                      }}
                    />
                  ) : null}
                </Stack>
              </Stack>
            </CustomTooltip>
          ))}
        </Stack>
      ) : null}
      <Menu
        anchorEl={menu?.el ?? null}
        anchorReference={menu?.pos ? "anchorPosition" : "anchorEl"}
        anchorPosition={menu?.pos}
        open={Boolean(menu)}
        onClose={() => setMenu(null)}
        onClick={(e) => e.stopPropagation()}
      >
        {!lane.blindName && !prod ? (
          <Box sx={{ px: 2, pt: 0.5, pb: 1 }}>
            <Typography typography="s3" color="text.secondary">
              {[kind, lane.label].filter(Boolean).join(" · ")}
            </Typography>
            <CopyId id={lane.run_id} label="Run" short={4} />
            <RunInfoRows prov={prov} modelName={modelName} />
          </Box>
        ) : null}
        {actions.map((a, i) =>
          a.divider ? (
            <Divider key={`d${i}`} />
          ) : (
            <CustomTooltip
              describeChild
              key={a.label}
              show
              size="small"
              placement="left"
              title={a.disabled ? a.reason ?? "" : a.tooltip ?? ""}
            >
              <span>
                <MenuItem
                  disabled={a.disabled}
                  onClick={() => {
                    setMenu(null);
                    a.onClick();
                  }}
                  sx={{
                    typography: "s2",
                    color: a.danger ? "error.main" : undefined,
                    minWidth: 260,
                  }}
                >
                  <ListItemIcon>
                    <Iconify
                      icon={a.icon}
                      width={18}
                      sx={{ color: a.danger ? "error.main" : undefined }}
                    />
                  </ListItemIcon>
                  <ListItemText primaryTypographyProps={{ typography: "s2" }}>
                    {a.label}
                  </ListItemText>
                  {a.keyCap ? (
                    <Typography
                      typography="s3"
                      color="text.disabled"
                      sx={{ ml: 2 }}
                    >
                      {a.keyCap}
                    </Typography>
                  ) : null}
                </MenuItem>
              </span>
            </CustomTooltip>
          ),
        )}
      </Menu>
    </Box>
  );
}

LaneHeader.propTypes = {
  lane: PropTypes.object,
  trial: PropTypes.object,
  baseTrial: PropTypes.object,
  isBaseline: PropTypes.bool,
  focused: PropTypes.bool,
  onFocus: PropTypes.func,
  onTrial: PropTypes.func,
  onStepTrial: PropTypes.func,
  fmt: PropTypes.string,
  onFmt: PropTypes.func,
  onDelta: PropTypes.func,
  blind: PropTypes.bool,
  actions: PropTypes.array,
  loading: PropTypes.bool,
  allTrials: PropTypes.bool,
  metricsAll: PropTypes.bool,
  displayName: PropTypes.string,
  // additive
  run: PropTypes.object,
  onTestFix: PropTypes.func,
};

export { nextFmt };
