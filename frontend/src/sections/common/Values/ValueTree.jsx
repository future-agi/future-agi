import React, { useCallback, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { copyToClipboard } from "src/utils/utils";
import { formatPath } from "./faPath";
import { MONO } from "./CodeBlock";

const PAGE = 200;
const CLAMP = 120;

const parseMaybe = (v) => {
  if (typeof v !== "string") return v;
  const t = v.trim();
  if (!(t.startsWith("{") || t.startsWith("["))) return v;
  try {
    return JSON.parse(t);
  } catch {
    return v;
  }
};

const isContainer = (v) => v !== null && typeof v === "object";
const countText = (v) =>
  Array.isArray(v)
    ? `${v.length.toLocaleString("en-US")} ${v.length === 1 ? "item" : "items"}`
    : `${Object.keys(v).length.toLocaleString("en-US")} ${Object.keys(v).length === 1 ? "key" : "keys"}`;

const keyOf = (steps) => JSON.stringify(steps);

/** Flat list of visible rows for the expanded set. */
function visibleRows(root, expanded, shown) {
  const rows = [];
  const walk = (value, steps, label, depth, inArray = false) => {
    const container = isContainer(value);
    const key = keyOf(steps);
    rows.push({ key, steps, label, value, depth, container, inArray });
    if (!container || !expanded.has(key)) return;
    if (Array.isArray(value)) {
      const limit = shown.get(key) ?? PAGE;
      value
        .slice(0, limit)
        .forEach((item, i) =>
          walk(
            parseMaybe(item),
            [...steps, { type: "index", index: i }],
            `${i}`,
            depth + 1,
            true,
          ),
        );
      if (value.length > limit)
        rows.push({
          key: `${key}#more`,
          more: true,
          parentKey: key,
          shown: limit,
          total: value.length,
          depth: depth + 1,
        });
    } else {
      Object.entries(value).forEach(([k, item]) =>
        walk(
          parseMaybe(item),
          [...steps, { type: "key", key: k }],
          k,
          depth + 1,
        ),
      );
    }
  };
  walk(root, [], null, 0);
  return rows;
}

function initialExpanded(root, depth = 2) {
  const out = new Set();
  const walk = (value, steps, d) => {
    if (!isContainer(value) || d >= depth) return;
    out.add(keyOf(steps));
    const entries = Array.isArray(value)
      ? value.slice(0, 20).map((v, i) => [{ type: "index", index: i }, v])
      : Object.entries(value).map(([k, v]) => [{ type: "key", key: k }, v]);
    entries.forEach(([s, v]) => walk(parseMaybe(v), [...steps, s], d + 1));
  };
  walk(root, [], 0);
  return out;
}

const Leaf = ({ value }) => {
  if (value === null)
    return (
      <Box component="span" sx={{ color: "text.disabled" }}>
        null
      </Box>
    );
  if (typeof value === "string") {
    const clamped = value.length > CLAMP;
    const text = JSON.stringify(clamped ? `${value.slice(0, CLAMP)}…` : value);
    const node = (
      <Box
        component="span"
        sx={{ color: "syntax.string", wordBreak: "break-word" }}
      >
        {text}
      </Box>
    );
    return clamped ? (
      <CustomTooltip
        show
        size="small"
        placement="top-start"
        title={value.length > 2000 ? `${value.slice(0, 2000)}…` : value}
      >
        {node}
      </CustomTooltip>
    ) : (
      node
    );
  }
  return (
    <Box
      component="span"
      sx={{
        color: typeof value === "number" ? "syntax.number" : "syntax.boolean",
      }}
    >
      {String(value)}
    </Box>
  );
};

Leaf.propTypes = { value: PropTypes.any };

/**
 * ValueTree — collapsible tree of an object / list (the `Tree` view mode).
 * Counts read `500 items` / `3 keys`; lists show 200 children at a time
 * (`Showing first 200 of 500 items · Show more`); a click selects a node
 * and offers `Copy path` (its FA path under `basePath`). Keyboard: ↑/↓
 * move, →/← expand/collapse, `C` copies the focused node's path.
 */
const ValueTree = ({
  value,
  basePath = null,
  maxHeight = 480,
  onAddColumn,
  onFilterBy,
  onUseAsVariable,
}) => {
  const root = useMemo(() => parseMaybe(value), [value]);
  // Small values open fully; large ones two levels deep.
  const [expanded, setExpanded] = useState(() => {
    let size = 0;
    try {
      size = JSON.stringify(root)?.length ?? 0;
    } catch {
      size = Infinity;
    }
    return initialExpanded(root, size < 2000 ? 6 : 2);
  });
  const [shown, setShown] = useState(() => new Map());
  const [focus, setFocus] = useState(0);
  const listRef = useRef(null);
  const rows = useMemo(
    () => visibleRows(root, expanded, shown),
    [root, expanded, shown],
  );
  const at = Math.min(focus, rows.length - 1);

  const pathOf = useCallback(
    (steps) => formatPath(basePath, steps) || basePath || "$",
    [basePath],
  );

  const copyPath = useCallback(
    (steps) => {
      const p = pathOf(steps);
      copyToClipboard(p);
      enqueueSnackbar(`Path copied · ${p}`, { variant: "success" });
    },
    [pathOf],
  );

  const toggle = (key, open) =>
    setExpanded((s) => {
      const next = new Set(s);
      const want = open ?? !next.has(key);
      if (want) next.add(key);
      else next.delete(key);
      return next;
    });

  const focusRow = (i) => {
    setFocus(i);
    const el = listRef.current?.querySelector(`[data-tree-index="${i}"]`);
    el?.focus();
  };

  const onKeyDown = (e) => {
    const row = rows[at];
    if (!row) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      focusRow(Math.min(rows.length - 1, at + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      focusRow(Math.max(0, at - 1));
    } else if (e.key === "ArrowRight" && row.container) {
      e.preventDefault();
      toggle(row.key, true);
    } else if (e.key === "ArrowLeft" && row.container) {
      e.preventDefault();
      toggle(row.key, false);
    } else if (
      (e.key === "c" || e.key === "C") &&
      !e.metaKey &&
      !e.ctrlKey &&
      row.steps
    ) {
      e.preventDefault();
      e.stopPropagation();
      copyPath(row.steps);
    } else if (e.key === "Enter" && row.more) {
      e.preventDefault();
      setShown((m) => new Map(m).set(row.parentKey, row.shown + PAGE));
    }
  };

  return (
    <Box
      ref={listRef}
      role="tree"
      aria-label={basePath ? `${basePath} tree` : "Value tree"}
      onKeyDown={onKeyDown}
      sx={{
        fontFamily: MONO,
        typography: "s2",
        maxHeight: maxHeight || undefined,
        overflow: "auto",
      }}
    >
      {rows.map((row, i) => {
        if (row.more)
          return (
            <Box
              key={row.key}
              data-tree-index={i}
              tabIndex={i === at ? 0 : -1}
              role="treeitem"
              onFocus={() => setFocus(i)}
              sx={{ pl: row.depth * 2 + 2.5, py: 0.25, outline: "none" }}
            >
              <Typography
                component="span"
                typography="s3"
                color="text.secondary"
                sx={{ fontFamily: "inherit" }}
              >
                {`Showing first ${row.shown.toLocaleString("en-US")} of ${row.total.toLocaleString("en-US")} items · `}
              </Typography>
              <Button
                size="small"
                variant="text"
                onClick={() =>
                  setShown((m) =>
                    new Map(m).set(row.parentKey, row.shown + PAGE),
                  )
                }
                sx={{
                  p: 0,
                  minWidth: 0,
                  typography: "s3",
                  verticalAlign: "baseline",
                }}
              >
                Show more
              </Button>
            </Box>
          );
        const open = row.container && expanded.has(row.key);
        const selected = i === at;
        return (
          <Box
            key={row.key}
            data-tree-index={i}
            role="treeitem"
            aria-expanded={row.container ? open : undefined}
            aria-level={row.depth + 1}
            tabIndex={selected ? 0 : -1}
            onFocus={() => setFocus(i)}
            onClick={() => {
              setFocus(i);
              if (row.container) toggle(row.key);
            }}
            sx={{
              display: "flex",
              flexWrap: "wrap",
              alignItems: "flex-start",
              gap: 0.5,
              pl: row.depth * 2,
              pr: 0.5,
              py: 0.125,
              minHeight: 24,
              borderRadius: 0.5,
              cursor: row.container ? "pointer" : "default",
              outline: "none",
              bgcolor: selected
                ? (t) => alpha(t.palette.primary.main, 0.08)
                : "transparent",
              "&:hover": { bgcolor: "action.hover" },
              "&:hover .tree-copy, &:focus .tree-copy": {
                visibility: "visible",
              },
            }}
          >
            <Box sx={{ width: 16, flexShrink: 0, display: "flex", pt: 0.25 }}>
              {row.container && (
                <Iconify
                  icon={
                    open ? "eva:chevron-down-fill" : "eva:chevron-right-fill"
                  }
                  width={16}
                  sx={{ color: "text.secondary" }}
                />
              )}
            </Box>
            <Box sx={{ flex: 1, minWidth: 0, lineHeight: "20px" }}>
              {row.label !== null && (
                <Box component="span" sx={{ color: "text.secondary" }}>
                  {row.inArray ? `[${row.label}]` : row.label}
                  {": "}
                </Box>
              )}
              {row.container ? (
                <Box component="span" sx={{ color: "text.disabled" }}>
                  {Array.isArray(row.value) ? "[" : "{"}
                  {countText(row.value)}
                  {Array.isArray(row.value) ? "]" : "}"}
                </Box>
              ) : (
                <Leaf value={row.value} />
              )}
            </Box>
            {/* IT-R2 G5 P5: the selected node's actions sit on their own
                line under it (they never squeeze the value). */}
            {selected && (row.steps.length > 0 || basePath) ? (
              <Box
                className="tree-copy"
                sx={{
                  flexBasis: "100%",
                  display: "flex",
                  flexWrap: "wrap",
                  gap: 0.25,
                  pl: 2.5,
                  pb: 0.25,
                }}
              >
                <CustomTooltip
                  show
                  size="small"
                  placement="top"
                  title={`Copy path · ${pathOf(row.steps)}`}
                >
                  <Button
                    size="small"
                    variant="text"
                    tabIndex={-1}
                    onClick={(e) => {
                      e.stopPropagation();
                      copyPath(row.steps);
                    }}
                    startIcon={<Iconify icon="mdi:content-copy" width={14} />}
                    sx={{
                      py: 0,
                      px: 0.75,
                      minWidth: 0,
                      typography: "s3",
                      height: 20,
                    }}
                  >
                    Copy path
                  </Button>
                </CustomTooltip>
                {/* IT-R1 G5 P8: a nested field becomes a grid column. */}
                {onAddColumn && row.steps.length > 0 ? (
                  <Button
                    size="small"
                    variant="text"
                    tabIndex={-1}
                    onClick={(e) => {
                      e.stopPropagation();
                      onAddColumn(pathOf(row.steps));
                    }}
                    startIcon={
                      <Iconify icon="mdi:table-column-plus-after" width={14} />
                    }
                    sx={{
                      py: 0,
                      px: 0.75,
                      minWidth: 0,
                      typography: "s3",
                      height: 20,
                    }}
                  >
                    Add as column
                  </Button>
                ) : null}
                {/* IT-R2 G5 P5: filter the grid by this value; map it. */}
                {onFilterBy && row.steps.length > 0 && !row.container ? (
                  <Button
                    size="small"
                    variant="text"
                    tabIndex={-1}
                    onClick={(e) => {
                      e.stopPropagation();
                      onFilterBy(pathOf(row.steps), row.value);
                    }}
                    startIcon={<Iconify icon="mdi:filter-outline" width={14} />}
                    sx={{
                      py: 0,
                      px: 0.75,
                      minWidth: 0,
                      typography: "s3",
                      height: 20,
                    }}
                  >
                    Filter by this
                  </Button>
                ) : null}
                {onUseAsVariable && row.steps.length > 0 ? (
                  <CustomTooltip
                    show
                    title={`Copies {{${pathOf(row.steps)}}} for a prompt or a run's variables`}
                    size="small"
                    type="black"
                  >
                    <Button
                      size="small"
                      variant="text"
                      tabIndex={-1}
                      onClick={(e) => {
                        e.stopPropagation();
                        onUseAsVariable(pathOf(row.steps));
                      }}
                      startIcon={<Iconify icon="mdi:code-braces" width={14} />}
                      sx={{
                        py: 0,
                        px: 0.75,
                        minWidth: 0,
                        typography: "s3",
                        height: 20,
                      }}
                    >
                      Use as variable
                    </Button>
                  </CustomTooltip>
                ) : null}
              </Box>
            ) : null}
          </Box>
        );
      })}
    </Box>
  );
};

ValueTree.propTypes = {
  value: PropTypes.any,
  basePath: PropTypes.string,
  maxHeight: PropTypes.number,
  onAddColumn: PropTypes.func,
  onFilterBy: PropTypes.func,
  onUseAsVariable: PropTypes.func,
};

export default ValueTree;
