import React, { memo, useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  IconButton,
  InputBase,
  LinearProgress,
  Menu,
  Divider,
  ListSubheader,
  MenuItem,
  Skeleton,
  Stack,
  Typography,
  useMediaQuery,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import Truncate from "src/sections/common/ux/Truncate";
import KeyHintTooltip from "src/sections/common/ux/KeyHintTooltip";
import KeyCap from "src/sections/common/ux/KeyCap";
import {
  FAILED_PREFIX,
  RAIL_FILTERS,
  dotOf,
  railFilterOf,
  reviewedCount,
} from "./workspaceModel";
import OneLine from "./OneLine";

export const RAIL_COLLAPSED = 48;
/** One rail (A4-13): 232 px, a dot per run (lane runs full, others dim). */
export const RAIL_WIDTH = 232;

/** One rail dot: shape = outcome, colour = run (never colour alone). */
export const RailDot = memo(function RailDot({ cell, run, dim, size = 10 }) {
  const d = dotOf(cell, run.label);
  const c = run.color?.solid ?? "#999";
  const base = {
    width: size,
    height: size,
    borderRadius: "50%",
    flexShrink: 0,
    boxSizing: "border-box",
    opacity: dim ? 0.35 : 1,
    borderStyle: d.carried ? "dashed" : "solid",
    borderWidth: 1.5,
    borderColor: c,
  };
  let sx = base;
  let inner = null;
  if (d.tone === "pass") sx = { ...base, bgcolor: c };
  else if (d.tone === "flaky")
    sx = {
      ...base,
      background: `linear-gradient(90deg, ${c} 50%, transparent 50%)`,
    };
  else if (d.tone === "error") {
    sx = {
      ...base,
      borderColor: "error.main",
      display: "grid",
      placeItems: "center",
    };
    inner = (
      <Box
        component="span"
        sx={{
          fontSize: 8,
          lineHeight: 1,
          fontWeight: 700,
          color: "error.main",
        }}
      >
        !
      </Box>
    );
  } else if (d.tone === "none")
    sx = {
      width: size - 2,
      height: 2,
      mx: "1px",
      bgcolor: "text.disabled",
      flexShrink: 0,
      opacity: dim ? 0.35 : 1,
      borderRadius: 1,
    };
  return (
    <CustomTooltip describeChild show size="small" title={d.label}>
      <Box component="span" aria-label={d.label} sx={sx}>
        {inner}
      </Box>
    </CustomTooltip>
  );
});

RailDot.propTypes = {
  cell: PropTypes.object,
  run: PropTypes.object.isRequired,
  dim: PropTypes.bool,
  size: PropTypes.number,
};

/** `2 of 3 passed` over the compared runs (every trial passed = passed). */
export function passedOf(row, runs) {
  const tones = runs.map((r) => dotOf(row.cells?.[r.run_id]).tone);
  const counted = tones.filter((t) => t !== "none");
  return {
    pass: tones.filter((t) => t === "pass").length,
    total: counted.length,
  };
}

const RailRow = memo(function RailRow({
  row,
  runs,
  laneSet,
  current,
  state,
  onSelect,
}) {
  // A dot per run (lane runs full opacity, the others dimmed); the pass
  // count is over the lanes on screen.
  const laneRuns = runs.filter((r) => laneSet.has(r.run_id));
  const passed = laneRuns.length ? passedOf(row, laneRuns) : null;
  const ref = useRef(null);
  useEffect(() => {
    if (current) ref.current?.scrollIntoView({ block: "nearest" });
  }, [current]);
  const viewed = state?.viewed || state?.verdicts > 0;
  return (
    <Box
      ref={ref}
      component="button"
      type="button"
      onClick={() => onSelect(row.row_id)}
      aria-current={current ? "true" : undefined}
      data-row-label={row.label}
      sx={{
        all: "unset",
        boxSizing: "border-box",
        display: "block",
        width: "100%",
        px: 1.25,
        py: 0.625,
        cursor: "pointer",
        borderLeft: "3px solid",
        borderColor: current ? "primary.main" : "transparent",
        bgcolor: current ? "action.selected" : "transparent",
        "&:hover": { bgcolor: current ? "action.selected" : "action.hover" },
        "&:focus-visible": {
          outline: "2px solid",
          outlineColor: "primary.main",
          outlineOffset: -2,
        },
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{ minWidth: 0 }}
      >
        <Box
          sx={{
            width: 12,
            flexShrink: 0,
            display: "grid",
            placeItems: "center",
          }}
        >
          {viewed ? (
            <CustomTooltip
              describeChild
              show
              size="small"
              title={
                state?.last_choice
                  ? "Reviewed: you judged this task"
                  : "Reviewed"
              }
            >
              <Iconify
                icon="mdi:check"
                width={13}
                sx={{ color: "success.main" }}
                aria-label="Reviewed"
              />
            </CustomTooltip>
          ) : null}
        </Box>
        <Typography
          typography="s2"
          fontWeight={current ? 700 : 600}
          sx={{
            flexShrink: 0,
            fontVariantNumeric: "tabular-nums",
            minWidth: 26,
          }}
        >
          {row.label}
        </Typography>
        <Box sx={{ minWidth: 0, flex: 1 }}>
          <OneLine
            text={row.title ?? ""}
            sx={{ typography: "s3", color: "text.secondary" }}
          />
        </Box>
      </Stack>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.5}
        sx={{ mt: 0.5, pl: "38px", minHeight: 12 }}
      >
        {runs.map((r) => (
          <RailDot
            key={r.run_id}
            cell={row.cells?.[r.run_id]}
            run={r}
            dim={!laneSet.has(r.run_id)}
          />
        ))}
        {passed ? (
          <Typography
            typography="s3"
            color="text.secondary"
            sx={{
              pl: 0.5,
              whiteSpace: "nowrap",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {passed.total ? `${passed.pass}/${passed.total} passed` : "not run"}
          </Typography>
        ) : null}
        <Box sx={{ flex: 1 }} />
        {state?.notes ? (
          <CustomTooltip
            describeChild
            show
            size="small"
            title={`${state.notes} ${state.notes === 1 ? "note" : "notes"} on this task`}
          >
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.25}
              sx={{ color: "text.secondary" }}
            >
              <Iconify icon="mdi:comment-text-outline" width={12} />
              <Typography
                typography="s3"
                sx={{ fontVariantNumeric: "tabular-nums" }}
              >
                {state.notes}
              </Typography>
            </Stack>
          </CustomTooltip>
        ) : null}
      </Stack>
    </Box>
  );
});

RailRow.propTypes = {
  row: PropTypes.object.isRequired,
  runs: PropTypes.array.isRequired,
  laneSet: PropTypes.object.isRequired,
  current: PropTypes.bool,
  state: PropTypes.object,
  onSelect: PropTypes.func.isRequired,
};

const glyph = (kind) => ({
  display: "inline-block",
  width: 9,
  height: 9,
  borderRadius: "50%",
  border: "1.5px solid",
  borderColor: "text.secondary",
  boxSizing: "border-box",
  verticalAlign: "-1px",
  mr: 0.5,
  ...(kind === "pass" ? { bgcolor: "text.secondary" } : null),
  ...(kind === "flaky"
    ? {
        background: (t) =>
          `linear-gradient(90deg, ${t.palette.text.secondary} 50%, transparent 50%)`,
      }
    : null),
});

/** `● passed  ○ failed  ◐ mixed` (colour = run; the tooltip says mixed = across tries). */
function RailLegend() {
  return (
    <CustomTooltip
      describeChild
      show
      size="small"
      title="One dot per run, in the run's colour. Filled: passed every try. Hollow: failed every try. Half: passed some tries, failed others."
    >
      <Typography
        typography="s3"
        color="text.secondary"
        sx={{ px: 1.25, pb: 0.5, fontSize: 11, whiteSpace: "nowrap" }}
        data-rail-legend
      >
        <Box component="span" sx={glyph("pass")} />
        passed{"  "}
        <Box component="span" sx={{ ...glyph("fail"), ml: 1 }} />
        failed{"  "}
        <Box component="span" sx={{ ...glyph("flaky"), ml: 1 }} />
        mixed
      </Typography>
    </CustomTooltip>
  );
}

/** The 48 px strip below 1440 px: task number + a tiny dot per compared run. */
function RailStrip({
  rows,
  runs,
  currentRowId,
  onSelectRow,
  onExpand,
  done,
  total,
}) {
  return (
    <Stack
      sx={{
        width: RAIL_COLLAPSED,
        height: "100%",
        minHeight: 0,
        borderRight: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
      }}
    >
      <CustomTooltip describeChild show size="small" title="Show the task list">
        <IconButton
          size="small"
          onClick={onExpand}
          aria-label="Show the task list"
          sx={{ mx: "auto", mt: 0.5, mb: 0.25 }}
        >
          <Iconify icon="mdi:chevron-double-right" width={18} />
        </IconButton>
      </CustomTooltip>
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          scrollbarWidth: "none",
        }}
      >
        {rows.map((r) => {
          const current = r.row_id === currentRowId;
          return (
            <Box
              key={r.row_id}
              component="button"
              type="button"
              aria-label={`${r.label} · ${r.title ?? ""}`}
              aria-current={current ? "true" : undefined}
              onClick={() => onSelectRow(r.row_id)}
              sx={{
                all: "unset",
                boxSizing: "border-box",
                display: "block",
                width: "100%",
                py: 0.5,
                cursor: "pointer",
                textAlign: "center",
                borderLeft: "3px solid",
                borderColor: current ? "primary.main" : "transparent",
                bgcolor: current ? "action.selected" : "transparent",
                "&:hover": { bgcolor: "action.hover" },
                "&:focus-visible": {
                  outline: "2px solid",
                  outlineColor: "primary.main",
                  outlineOffset: -2,
                },
              }}
            >
              <Typography
                typography="s3"
                fontWeight={current ? 700 : 600}
                sx={{ display: "block", fontVariantNumeric: "tabular-nums" }}
              >
                {r.label}
              </Typography>
              <Stack
                direction="row"
                spacing="2px"
                justifyContent="center"
                sx={{ mt: "2px" }}
              >
                {runs.map((run) => (
                  <RailDot
                    key={run.run_id}
                    cell={r.cells?.[run.run_id]}
                    run={run}
                    size={7}
                  />
                ))}
              </Stack>
            </Box>
          );
        })}
      </Box>
      {/* IT-R4 G4 P5: the counter stays pinned under the scrolling list
          and explains the dots. */}
      <CustomTooltip
        describeChild
        show
        size="small"
        title={`${done} of ${total} tasks viewed. Dots: one per run, in the run's colour. Filled: passed every try · hollow: failed every try · half: passed some tries.`}
      >
        <Typography
          typography="s3"
          color="text.secondary"
          tabIndex={0}
          data-testid="tc-rail-viewed"
          sx={{
            textAlign: "center",
            py: 0.5,
            fontSize: 10,
            flexShrink: 0,
            borderTop: "1px solid",
            borderColor: "divider",
            fontVariantNumeric: "tabular-nums",
            cursor: "help",
          }}
        >
          {done}/{total}
          <br />
          viewed
        </Typography>
      </CustomTooltip>
    </Stack>
  );
}
RailStrip.propTypes = {
  rows: PropTypes.array.isRequired,
  runs: PropTypes.array.isRequired,
  currentRowId: PropTypes.string,
  onSelectRow: PropTypes.func.isRequired,
  onExpand: PropTypes.func.isRequired,
  done: PropTypes.number,
  total: PropTypes.number,
};

/**
 * The task rail: filter (`Differ 17 ▾`), search, one row per task with a dot
 * per run in the selection (lane runs full, others 40%), viewed ticks, note
 * glyphs, and `9 of 17 reviewed`. Collapses to 44 px (remembered).
 */
export default function TaskRail({
  rows,
  sets,
  rail,
  onRail,
  currentRowId,
  onSelectRow,
  runs,
  laneIds,
  rowsState,
  width,
  collapsed,
  onCollapse,
  loading,
  searchRef,
  focusId,
  baselineId,
  note,
}) {
  // Search: a field from 1440 px; below, an icon that opens it (click, or
  // `/` while the rail has focus).
  const roomy = useMediaQuery("(min-width:1440px)");
  const [searchOpen, setSearchOpen] = useState(false);
  const [anchor, setAnchor] = useState(null);
  const [peek, setPeek] = useState(false);
  const peekTimer = useRef(null);
  const [q, setQ] = useState("");
  const laneSet = useMemo(() => new Set(laneIds), [laneIds]);
  const byId = useMemo(() => new Map(rows.map((r) => [r.row_id, r])), [rows]);
  const ids = sets?.[rail] ?? [];
  const needle = q.trim().toLowerCase();
  const shown = ids
    .map((id) => byId.get(id))
    .filter(Boolean)
    .filter(
      (r) => !needle || `${r.label} ${r.title}`.toLowerCase().includes(needle),
    );
  // The current row stays visible (pinned) even when the filter hides it.
  const pinned =
    currentRowId && !shown.some((r) => r.row_id === currentRowId)
      ? byId.get(currentRowId)
      : null;
  const { done, total } = reviewedCount(ids, rowsState);
  const runLabelOf = (id) =>
    runs.find((r) => r.run_id === id)?.label ?? "a run";
  const filter = railFilterOf(rail, {
    labelOf: runLabelOf,
    focusId,
    baselineId,
  });
  const label = filter?.label ?? "All";
  const laneRuns = runs.filter((r) => laneSet.has(r.run_id));

  const showField = roomy || searchOpen || Boolean(q);
  const openSearch = () => {
    setSearchOpen(true);
    setTimeout(() => searchRef?.current?.focus(), 30);
  };
  const fullRail = (overlay) => (
    <Stack
      component="nav"
      aria-label="Tasks"
      data-rail
      onKeyDown={(e) => {
        const t = e.target;
        const typing = t?.tagName === "INPUT" || t?.tagName === "TEXTAREA";
        if (e.key === "/" && !typing && !showField) {
          e.preventDefault();
          openSearch();
        }
      }}
      sx={{
        width,
        flexShrink: 0,
        borderRight: "1px solid",
        borderColor: "divider",
        minHeight: 0,
        bgcolor: "background.paper",
        ...(overlay
          ? {
              position: "absolute",
              left: 0,
              top: 0,
              bottom: 0,
              zIndex: 20,
              boxShadow: 8,
            }
          : null),
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.5}
        sx={{ px: 1, pt: 1, pb: 0.5 }}
      >
        <CustomTooltip describeChild show size="small" title={filter?.help}>
          <Button
            size="small"
            color="inherit"
            onClick={(e) => setAnchor(e.currentTarget)}
            endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
            sx={{
              typography: "s2",
              fontWeight: 600,
              px: 0.75,
              minWidth: 0,
              flexShrink: 0,
            }}
            aria-haspopup="menu"
          >
            {`${filter?.long ?? label} (${ids.length})`}
          </Button>
        </CustomTooltip>
        <Box sx={{ flex: 1 }} />
        {!showField ? (
          <KeyHintTooltip title="Find a task" action="search">
            <IconButton
              size="small"
              onClick={openSearch}
              aria-label="Find a task"
            >
              <Iconify icon="mdi:magnify" width={18} />
            </IconButton>
          </KeyHintTooltip>
        ) : null}
        <CustomTooltip
          describeChild
          show
          size="small"
          title="Hide the task list"
        >
          <IconButton
            size="small"
            onClick={() => onCollapse(true)}
            aria-label="Hide the task list"
          >
            <Iconify icon="mdi:chevron-double-left" width={18} />
          </IconButton>
        </CustomTooltip>
      </Stack>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
      >
        {RAIL_FILTERS.map((f) => (
          <MenuItem
            key={f.value}
            selected={f.value === rail}
            onClick={() => {
              onRail(f.value);
              setAnchor(null);
            }}
            sx={{ typography: "s2", minWidth: 220 }}
          >
            <CustomTooltip
              describeChild
              show
              size="small"
              placement="right"
              title={f.help}
            >
              <Stack direction="row" sx={{ width: "100%" }}>
                <span>{f.label}</span>
                <Box sx={{ flex: 1 }} />
                <Typography
                  typography="s2"
                  color="text.secondary"
                  sx={{ fontVariantNumeric: "tabular-nums", ml: 2 }}
                >
                  {sets?.[f.value]?.length ?? 0}
                </Typography>
              </Stack>
            </CustomTooltip>
          </MenuItem>
        ))}
        <Divider />
        <ListSubheader
          sx={{ typography: "s3", lineHeight: "28px", bgcolor: "transparent" }}
        >
          Failed by
        </ListSubheader>
        {[
          { value: `${FAILED_PREFIX}any`, label: "Any run" },
          ...laneRuns.map((r) => ({
            value: `${FAILED_PREFIX}${r.run_id}`,
            label: [r.label, r.name].filter(Boolean).join(" "),
          })),
        ].map((f) => (
          <MenuItem
            key={f.value}
            selected={f.value === rail}
            onClick={() => {
              onRail(f.value);
              setAnchor(null);
            }}
            sx={{ typography: "s2", minWidth: 220 }}
          >
            <Stack direction="row" sx={{ width: "100%", minWidth: 0 }}>
              <Box
                component="span"
                sx={{
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  maxWidth: 220,
                }}
              >
                {f.label}
              </Box>
              <Box sx={{ flex: 1 }} />
              <Typography
                typography="s2"
                color="text.secondary"
                sx={{ fontVariantNumeric: "tabular-nums", ml: 2 }}
              >
                {sets?.[f.value]?.length ?? 0}
              </Typography>
            </Stack>
          </MenuItem>
        ))}
      </Menu>
      <RailLegend />
      {note ? (
        // IT-R5 G4 P4 (rule 39): why the rail shows every task.
        <Typography
          typography="s3"
          color="text.secondary"
          data-testid="tc-rail-note"
          sx={{ px: 1.25, pb: 0.5, fontSize: 11 }}
        >
          {note}
        </Typography>
      ) : null}
      <Box sx={{ px: 1, pb: 0.75, display: showField ? "block" : "none" }}>
        <Stack
          direction="row"
          alignItems="center"
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            px: 0.75,
            height: 28,
          }}
        >
          <Iconify
            icon="mdi:magnify"
            width={15}
            sx={{ color: "text.disabled", flexShrink: 0 }}
          />
          <InputBase
            inputRef={searchRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) =>
              e.key === "Escape" &&
              (e.stopPropagation(),
              setQ(""),
              setSearchOpen(false),
              e.currentTarget.blur())
            }
            onBlur={() => !q && setSearchOpen(false)}
            placeholder="Find a task"
            inputProps={{ "aria-label": "Find a task" }}
            sx={{ ml: 0.5, flex: 1, typography: "s2" }}
          />
          {!q ? <KeyCap>/</KeyCap> : null}
        </Stack>
      </Box>
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        {loading ? (
          Array.from({ length: 12 }).map((_, i) => (
            <Box key={i} sx={{ px: 1.25, py: 0.75 }}>
              <Skeleton variant="text" width="70%" height={16} />
              <Skeleton variant="text" width="40%" height={12} />
            </Box>
          ))
        ) : (
          <>
            {pinned ? (
              <Box sx={{ borderBottom: "1px dashed", borderColor: "divider" }}>
                <RailRow
                  row={pinned}
                  runs={runs}
                  laneSet={laneSet}
                  current
                  state={rowsState?.[pinned.row_id]}
                  onSelect={onSelectRow}
                />
              </Box>
            ) : null}
            {shown.map((r) => (
              <RailRow
                key={r.row_id}
                row={r}
                runs={runs}
                laneSet={laneSet}
                current={r.row_id === currentRowId}
                state={rowsState?.[r.row_id]}
                onSelect={onSelectRow}
              />
            ))}
            {!shown.length ? (
              <Box sx={{ px: 1.5, py: 2 }}>
                <Typography typography="s2" color="text.secondary">
                  {needle
                    ? `No tasks match "${q}".`
                    : `No tasks match "${label}".`}
                </Typography>
                <Button
                  size="small"
                  sx={{ mt: 0.5, px: 0 }}
                  onClick={() => (setQ(""), onRail("all"))}
                >
                  Show all {sets?.all?.length ?? 0}
                </Button>
              </Box>
            ) : null}
          </>
        )}
      </Box>
      <Box
        sx={{
          px: 1.25,
          py: 0.75,
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        <Stack
          direction="row"
          alignItems="baseline"
          justifyContent="space-between"
        >
          <Typography
            typography="s2"
            sx={{ fontVariantNumeric: "tabular-nums" }}
          >
            {done} of {total} reviewed
          </Typography>
        </Stack>
        <LinearProgress
          variant="determinate"
          value={total ? (done / total) * 100 : 0}
          sx={{ mt: 0.5, height: 3, borderRadius: 2 }}
          aria-label={`${done} of ${total} reviewed`}
        />
        <KeyHintTooltip
          title="Next task you have not reviewed"
          action="nextUnreviewed"
        >
          <Typography typography="s3" color="text.secondary" sx={{ mt: 0.5 }}>
            <KeyCap>⇧J</KeyCap> next unreviewed
          </Typography>
        </KeyHintTooltip>
      </Box>
    </Stack>
  );

  if (!collapsed) return fullRail(false);
  const open = () => {
    clearTimeout(peekTimer.current);
    peekTimer.current = setTimeout(() => setPeek(true), 120);
  };
  const close = () => {
    // The filter menu portals out of the strip: keep the overlay while open.
    if (anchor) return;
    clearTimeout(peekTimer.current);
    peekTimer.current = setTimeout(() => setPeek(false), 200);
  };
  return (
    <Box
      sx={{
        position: "relative",
        width: RAIL_COLLAPSED,
        flexShrink: 0,
        minHeight: 0,
      }}
      onMouseEnter={open}
      onMouseLeave={close}
      onFocus={open}
      onBlur={(e) => !e.currentTarget.contains(e.relatedTarget) && close()}
    >
      <RailStrip
        rows={pinned ? [pinned, ...shown] : shown}
        runs={laneRuns}
        currentRowId={currentRowId}
        onSelectRow={onSelectRow}
        onExpand={() => (setPeek(false), onCollapse(false))}
        done={done}
        total={total}
      />
      {peek ? fullRail(true) : null}
    </Box>
  );
}

TaskRail.propTypes = {
  rows: PropTypes.array.isRequired,
  sets: PropTypes.object,
  focusId: PropTypes.string,
  baselineId: PropTypes.string,
  rail: PropTypes.string.isRequired,
  note: PropTypes.string,
  onRail: PropTypes.func.isRequired,
  currentRowId: PropTypes.string,
  onSelectRow: PropTypes.func.isRequired,
  runs: PropTypes.array.isRequired,
  laneIds: PropTypes.array.isRequired,
  rowsState: PropTypes.object,
  width: PropTypes.number.isRequired,
  collapsed: PropTypes.bool,
  onCollapse: PropTypes.func.isRequired,
  loading: PropTypes.bool,
  searchRef: PropTypes.object,
};
