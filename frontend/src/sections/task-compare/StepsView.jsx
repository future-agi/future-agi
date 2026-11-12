import React, { Fragment, useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, InputBase, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import KeyHintTooltip from "src/sections/common/ux/KeyHintTooltip";
import KeyCap from "src/sections/common/ux/KeyCap";
import { OutlineToggle } from "src/sections/common/ux/oneview";
import LanesGrid, { laneTemplate } from "./LanesGrid";
import { OneStep } from "./StepCell";
import { exactCall, gapLabel } from "./stepModel";
import { gapId, lineId } from "./oneViewParams";

const CHANGE = {
  same: { glyph: "=", label: "Same as the baseline", color: "text.disabled" },
  input: { glyph: "~", label: "Input changed", color: "warning.main" },
  output: { glyph: "~", label: "Output changed", color: "warning.main" },
  error: { glyph: "~", label: "Error on one side", color: "error.main" },
  only: { glyph: "+", label: "Only here", color: "info.main" },
  missing: { glyph: "−", label: "Missing here", color: "text.secondary" },
};

/** Gutter change of lane `l` vs the baseline in one row. */
export function changeOf(row, l) {
  const base = row.cells[0];
  const cell = row.cells[l];
  if (row.kind === "gap") {
    if (cell) return "only";
    if (base) return "missing";
    return null;
  }
  if (!base && !cell) return null;
  if (base && !cell) return "missing";
  if (!base && cell) return "only";
  if (cell.inputChanged) return "input";
  if (cell.errorChanged) return "error";
  if (cell.outputChanged) return "output";
  return "same";
}

const hatch = (t) =>
  `repeating-linear-gradient(135deg, ${t.palette.action.hover} 0 6px, transparent 6px 12px)`;

function Gutter({ change }) {
  if (!change) return <Box />;
  const c = CHANGE[change];
  return (
    <CustomTooltip describeChild show size="small" title={c.label}>
      <Box sx={{ display: "flex", justifyContent: "center", pt: 0.75 }}>
        <Box
          component="span"
          aria-label={c.label}
          sx={{
            width: 18,
            height: 18,
            borderRadius: "50%",
            display: "grid",
            placeItems: "center",
            typography: "s2",
            fontWeight: 700,
            color: c.color,
            bgcolor: change === "same" ? "transparent" : "action.hover",
          }}
        >
          {c.glyph}
        </Box>
      </Box>
    </CustomTooltip>
  );
}
Gutter.propTypes = { change: PropTypes.string };

const textOf = (built) =>
  built.cells
    .flatMap((c) => c.lines)
    .map((ln) => `${ln.text} ${exactCall(ln.step)}`)
    .join(" ")
    .toLowerCase();

/**
 * The steps, one view (ONE-VIEW-SPEC §7.1 item 4 + §7.2): one toolbar
 * (`Differences only` · `Find in steps` · `4 differences ↑ ↓` · `Expand
 * all` · spacer · `Map` / `First wrong step ↵` when the map is collapsed ·
 * `View ▾`), then aligned rows: one plain line per step that opens in
 * place to its exact call, gutters `= ~ + −` per non-baseline lane, gap
 * rows with an outline `+`, the first wrong step with its red rule.
 */
export default function StepsView({
  built,
  lanes,
  header,
  view,
  setView,
  diffOnly,
  outline,
  onAcross,
  anchor,
  firstDifferenceRowId,
  query,
  onQuery,
  searchRef,
  diffNav,
  toolbarStart,
  toolbarEnd,
  toolbarBelow,
  fwNote,
  checkName,
  promptOf,
  onOpenTrace,
  onOpenSpan,
  onFixStep,
  canFixStep,
}) {
  const n = lanes.length;
  const [searchFocus, setSearchFocus] = useState(false);
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (built?.rows ?? []).filter(
      (b) =>
        (!diffOnly || b.row.kind === "gap" || b.row.change !== "same") &&
        (!q || textOf(b).includes(q)),
    );
  }, [built, diffOnly, query]);
  const maxMs = useMemo(
    () =>
      Math.max(
        1,
        ...lanes.flatMap((l) => (l.steps ?? []).map((s) => s.ms ?? 0)),
      ),
    [lanes],
  );
  useEffect(() => {
    if (!anchor) return;
    document
      .getElementById(`tc-row-${anchor}`)
      ?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [anchor]);

  const lineProps = (row, l, ln) => {
    const base =
      l > 0 && row.cells?.[0]?.type === "step" && ln.milestone
        ? row.cells[0].step
        : null;
    const id = lineId(row.id, lanes[l].id, ln.n);
    return {
      line: ln,
      open: outline.isOpen(id),
      onToggle: () => outline.toggle(id),
      onAcross: () => onAcross(row, l, ln.n),
      wrap: view.wrap,
      note: ln.firstWrong ? fwNote : null,
      checkName,
      base,
      isBase: l === 0,
      maxMs,
      color: lanes[l].color?.solid,
      prompt: promptOf(l, ln.n),
      showPrompts: view.prompts,
      model: lanes[l].modelOf?.(ln.n) ?? null,
      onOpenTrace: lanes[l].trial?.trace_id ? () => onOpenTrace(l) : null,
      onOpenSpan:
        onOpenSpan && lanes[l].trial?.trace_id
          ? () => onOpenSpan(l, ln.n)
          : null,
      onFixStep:
        ln.firstWrong && onFixStep && (!canFixStep || canFixStep(l))
          ? () => onFixStep(l, ln)
          : null,
    };
  };

  const toolbar = (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      data-testid="tc-toolbar"
      sx={{
        px: 1.5,
        height: 34,
        flexShrink: 0,
        borderBottom: "1px solid",
        borderColor: "divider",
        minWidth: 0,
        overflow: "hidden",
      }}
    >
      {toolbarStart}
      {/* IT-R4 G4 P5: Differences only, Expand all and Map live in the View
          menu (TcViewMenu); keys D-free: the shortcuts still work. */}
      <KeyHintTooltip title="Find in steps" action="search">
        <Stack
          direction="row"
          alignItems="center"
          sx={{
            border: "1px solid",
            borderColor: searchFocus ? "primary.main" : "divider",
            borderRadius: 1,
            px: 0.75,
            height: 26,
            width: searchFocus || query ? 240 : 160,
            transition: "width 120ms",
            flexShrink: 1,
            minWidth: 110,
          }}
        >
          <Iconify
            icon="mdi:magnify"
            width={14}
            sx={{ color: "text.disabled", flexShrink: 0 }}
          />
          <InputBase
            inputRef={searchRef}
            value={query}
            onChange={(e) => onQuery(e.target.value)}
            onFocus={() => setSearchFocus(true)}
            onBlur={() => setSearchFocus(false)}
            onKeyDown={(e) =>
              e.key === "Escape" &&
              (e.stopPropagation(), onQuery(""), e.currentTarget.blur())
            }
            placeholder="Find in steps"
            inputProps={{ "aria-label": "Find in steps" }}
            sx={{ ml: 0.5, typography: "s3", flex: 1, minWidth: 0 }}
          />
          {!query && !searchFocus ? <KeyCap>/</KeyCap> : null}
        </Stack>
      </KeyHintTooltip>
      <KeyHintTooltip
        title={
          diffNav.total
            ? "Next difference (Shift+click: previous)"
            : "The runs did the same here"
        }
        action="nextDiff"
      >
        <span>
          <Button
            size="small"
            color="inherit"
            disabled={!diffNav.total}
            onClick={(e) => (e.shiftKey ? diffNav.prev() : diffNav.next())}
            endIcon={
              diffNav.total ? (
                <Iconify icon="mdi:chevron-down" width={16} />
              ) : null
            }
            sx={{
              typography: "s2",
              color: "text.secondary",
              minWidth: 0,
              flexShrink: 0,
              fontVariantNumeric: "tabular-nums",
              whiteSpace: "nowrap",
            }}
          >
            {diffNav.total
              ? diffNav.index
                ? `Difference ${diffNav.index} of ${diffNav.total}`
                : `${diffNav.total} ${diffNav.total === 1 ? "difference" : "differences"}`
              : "No differences"}
          </Button>
        </span>
      </KeyHintTooltip>
      <Box sx={{ flex: 1 }} />
      {toolbarEnd}
    </Stack>
  );

  const renderCell = (b, l) => {
    const { row } = b;
    const { cell, lines } = b.cells[l];
    const lane = lanes[l];
    if (row.kind === "gap") {
      if (!cell)
        return <Box sx={{ borderTop: "1px solid", borderColor: "divider" }} />;
      const gid = gapId(row.id, lane.id);
      const open = outline.isOpen(gid);
      return (
        <Box
          sx={{
            borderTop: "1px solid",
            borderColor: "divider",
            background: open ? undefined : hatch,
            px: 1.25,
            py: 0.5,
            minWidth: 0,
          }}
        >
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <OutlineToggle
              open={open}
              onToggle={() => outline.toggle(gid)}
              label={`the ${cell.count} ${cell.count === 1 ? "step" : "steps"} only in ${lane.label}`}
            />
            <Typography
              typography="s2"
              color="text.secondary"
              onClick={() => outline.toggle(gid)}
              sx={{ cursor: "pointer", minWidth: 0 }}
            >
              {gapLabel(cell, lane.label)}
            </Typography>
          </Stack>
          {open ? (
            <Stack spacing={0.25} sx={{ mt: 0.5 }}>
              {lines.map((ln) => (
                <OneStep key={ln.n} {...lineProps(row, l, ln)} />
              ))}
            </Stack>
          ) : null}
        </Box>
      );
    }
    const peerOpen =
      !lines.some((ln) => outline.isOpen(lineId(row.id, lane.id, ln.n))) &&
      b.cells.some((c, o) =>
        c.lines.some((ln) => outline.isOpen(lineId(row.id, lanes[o].id, ln.n))),
      );
    return (
      <Box
        sx={{
          borderTop: "1px solid",
          borderColor: "divider",
          px: 1.25,
          py: 0.375,
          minWidth: 0,
          bgcolor: peerOpen ? "action.hover" : "transparent",
        }}
      >
        {lines.length ? (
          <Stack spacing={0.125}>
            {lines.map((ln) => (
              <OneStep key={ln.n} {...lineProps(row, l, ln)} />
            ))}
          </Stack>
        ) : (
          <Typography
            typography="s3"
            color="text.disabled"
            sx={{ pt: 0.5, pl: "26px" }}
          >
            {lane.noTrace ? "" : `Not in ${lane.label}`}
          </Typography>
        )}
      </Box>
    );
  };

  if (view.align === "none")
    return (
      <LanesGrid
        laneCount={n}
        header={header}
        toolbar={
          <>
            {toolbar}
            {toolbarBelow}
          </>
        }
      >
        <Box
          data-oneview-row
          sx={{ display: "grid", gridTemplateColumns: laneTemplate(n) }}
        >
          {lanes.map((l, i) => (
            <Stack
              key={l.id}
              spacing={0.125}
              sx={{
                px: 1.25,
                py: 0.75,
                borderLeft: i ? "1px solid" : 0,
                borderColor: "divider",
                minWidth: 0,
              }}
            >
              {(built?.rows?.[0]?.cells?.[i]?.lines ?? []).map((ln) => (
                <OneStep
                  key={ln.n}
                  {...lineProps(built.rows[0].row, i, ln)}
                  onAcross={undefined}
                />
              ))}
            </Stack>
          ))}
        </Box>
      </LanesGrid>
    );

  return (
    <LanesGrid
      laneCount={n}
      gutters
      header={header}
      toolbar={
        <>
          {toolbar}
          {toolbarBelow}
        </>
      }
    >
      {!rows.length ? (
        <Box sx={{ p: 3 }}>
          <Typography typography="s1">
            {query
              ? `No steps match "${query}".`
              : diffOnly
                ? "These runs took the same steps."
                : "No steps were recorded for these runs on this task."}
          </Typography>
          {query || diffOnly ? (
            <Button
              size="small"
              sx={{ mt: 1 }}
              onClick={() => (query ? onQuery("") : setView({ diff: false }))}
            >
              {query ? "Clear search" : "Show every step"}
            </Button>
          ) : null}
        </Box>
      ) : null}
      {rows.map((b) => {
        const { row } = b;
        const isAnchor = anchor === row.id;
        const isFD = firstDifferenceRowId === row.id;
        return (
          <Box
            key={row.id}
            id={`tc-row-${row.id}`}
            data-oneview-row
            sx={{
              display: "grid",
              gridTemplateColumns: laneTemplate(n, true),
              bgcolor: isAnchor ? "action.selected" : "transparent",
              boxShadow: isFD
                ? (t) =>
                    `inset 3px 0 0 ${t.palette.mode === "dark" ? "#C4B5FD" : "#7C3AED"}`
                : "none",
              scrollMarginTop: 80,
            }}
          >
            {b.cells.map((_, l) => (
              <Fragment key={l}>
                {l > 0 ? (
                  <Gutter change={lanes[l].noTrace ? null : changeOf(row, l)} />
                ) : null}
                {renderCell(b, l)}
              </Fragment>
            ))}
          </Box>
        );
      })}
      <Box sx={{ height: 24 }} />
    </LanesGrid>
  );
}

StepsView.propTypes = {
  built: PropTypes.object,
  lanes: PropTypes.array.isRequired,
  header: PropTypes.node,
  view: PropTypes.object.isRequired,
  setView: PropTypes.func.isRequired,
  diffOnly: PropTypes.bool,
  outline: PropTypes.object.isRequired,
  onAcross: PropTypes.func.isRequired,
  anchor: PropTypes.string,
  firstDifferenceRowId: PropTypes.string,
  query: PropTypes.string,
  onQuery: PropTypes.func.isRequired,
  searchRef: PropTypes.object,
  diffNav: PropTypes.object.isRequired,
  toolbarStart: PropTypes.node,
  onOpenSpan: PropTypes.func,
  onFixStep: PropTypes.func,
  // dataset integration FE-0: false for a lane Test a fix does not apply to (no button).
  canFixStep: PropTypes.func,
  toolbarEnd: PropTypes.node,
  // IT-R2 G4 (additive): a line under the toolbar (the graph hint).
  toolbarBelow: PropTypes.node,
  fwNote: PropTypes.string,
  checkName: PropTypes.func,
  promptOf: PropTypes.func.isRequired,
  onOpenTrace: PropTypes.func.isRequired,
};
