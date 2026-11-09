import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  Chip,
  IconButton,
  InputAdornment,
  Popover,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import KeyHintTooltip from "../KeyHintTooltip";
import { stableColumnRows } from "./columnLayout";
import useShortcuts from "../useShortcuts";
import { registerColumnsHost } from "./columnsMenuHost";

// IT-R1 G5 P11: a tooltip that closes on click (freezing a column re-renders
// the row; a hover tooltip left open would stay pinned over the list).
function ClickTip({ title, children }) {
  const [open, setOpen] = useState(false);
  return (
    <CustomTooltip
      show
      size="small"
      title={title}
      disableInteractive
      open={open}
      onOpen={() => setOpen(true)}
      onClose={() => setOpen(false)}
    >
      <Box
        component="span"
        sx={{ display: "inline-flex" }}
        onClickCapture={() => setOpen(false)}
      >
        {children}
      </Box>
    </CustomTooltip>
  );
}
ClickTip.propTypes = { title: PropTypes.node, children: PropTypes.node };

const tip = (title, node) => <ClickTip title={title}>{node}</ClickTip>;

// IT-R5 G3 (S3): why a column has a lock.
export const LOCK_TIP = "Row and Input stay frozen so you never lose the row";

/**
 * IT-R5 G3 (S3): the share of the grid's width its frozen columns take
 * (0..1), from the widest grid on the page; null when there is none.
 */
export function frozenShare(
  doc = typeof document === "undefined" ? null : document,
) {
  if (!doc) return null;
  const roots = [...doc.querySelectorAll(".ag-root-wrapper")].filter(
    (r) => r.getBoundingClientRect().width > 0,
  );
  const root = roots.sort(
    (a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width,
  )[0];
  if (!root) return null;
  const total = root.getBoundingClientRect().width;
  const pinned = root.querySelector(".ag-pinned-left-header");
  const w = pinned ? pinned.getBoundingClientRect().width : 0;
  return total > 0 ? w / total : null;
}

/** `Frozen columns take 58% of the width` above half; else null. Pure. */
export function frozenWarning(share) {
  if (!Number.isFinite(share) || share <= 0.5) return null;
  return `Frozen columns take ${Math.round(share * 100)}% of the width`;
}

function ColumnRow({
  col,
  sortable,
  focused,
  onFocusRow,
  layout,
  onKeyDown,
  rowRef,
}) {
  const s = useSortable({ id: col.id, disabled: !sortable || col.locked });
  const style = {
    transform: CSS.Transform.toString(s.transform),
    transition: s.transition,
  };
  const pinned = col.pinned === "left";
  return (
    <Stack
      ref={(el) => {
        s.setNodeRef(el);
        rowRef(el);
      }}
      style={style}
      direction="row"
      alignItems="center"
      role="option"
      aria-selected={!col.hidden}
      tabIndex={focused ? 0 : -1}
      data-col-row={col.id}
      onFocus={onFocusRow}
      onKeyDown={onKeyDown}
      sx={{
        height: 32,
        pl: 0.25,
        pr: 0.5,
        borderRadius: 0.75,
        bgcolor: s.isDragging ? "action.selected" : "transparent",
        position: "relative",
        zIndex: s.isDragging ? 1 : 0,
        "&:hover": { bgcolor: "action.hover" },
        "&:focus-visible": {
          outline: "2px solid",
          outlineColor: "primary.main",
          outlineOffset: -2,
        },
      }}
    >
      <Box
        {...(sortable && !col.locked ? s.attributes : {})}
        {...(sortable && !col.locked ? s.listeners : {})}
        tabIndex={-1}
        aria-label={`Drag ${col.label}`}
        sx={{
          width: 20,
          display: "flex",
          justifyContent: "center",
          color: "text.disabled",
          cursor: sortable && !col.locked ? "grab" : "default",
          visibility: sortable && !col.locked ? "visible" : "hidden",
          "&:active": { cursor: "grabbing" },
        }}
      >
        <Iconify icon="mdi:drag-vertical" width={16} />
      </Box>
      <Checkbox
        size="small"
        checked={!col.hidden}
        disabled={col.locked}
        tabIndex={-1}
        onChange={() => layout.toggle(col.id)}
        inputProps={{ "aria-label": `Show ${col.label}` }}
        sx={{ p: 0.5 }}
      />
      <Typography
        typography="s2"
        noWrap
        onClick={() => !col.locked && layout.toggle(col.id)}
        sx={{
          flex: 1,
          minWidth: 0,
          ml: 0.5,
          alignSelf: "stretch",
          display: "flex",
          alignItems: "center",
          cursor: col.locked ? "default" : "pointer",
          userSelect: "none",
          color: col.hidden ? "text.secondary" : "text.primary",
        }}
        title={col.label}
      >
        {col.label}
      </Typography>
      {col.locked &&
        tip(
          LOCK_TIP,
          <Box
            component="span"
            aria-label="Always shown"
            sx={{ display: "flex", color: "text.disabled", mr: 0.5 }}
          >
            <Iconify icon="mdi:lock-outline" width={14} />
          </Box>,
        )}
      {col.pinnable ? (
        tip(
          pinned ? "Unfreeze" : "Freeze",
          <IconButton
            size="small"
            tabIndex={-1}
            aria-label={`${pinned ? "Unfreeze" : "Freeze"} ${col.label}`}
            aria-pressed={pinned}
            onClick={() => layout.pin(col.id, pinned ? null : "left")}
            sx={{
              p: 0.5,
              color: pinned ? "primary.main" : "text.disabled",
              "&:hover": { color: pinned ? "primary.main" : "text.secondary" },
            }}
          >
            <Iconify icon={pinned ? "mdi:pin" : "mdi:pin-outline"} width={16} />
          </IconButton>,
        )
      ) : (
        <Box sx={{ width: 28 }} />
      )}
    </Stack>
  );
}

ColumnRow.propTypes = {
  col: PropTypes.object.isRequired,
  sortable: PropTypes.bool,
  focused: PropTypes.bool,
  onFocusRow: PropTypes.func,
  layout: PropTypes.object.isRequired,
  onKeyDown: PropTypes.func,
  rowRef: PropTypes.func,
};

/**
 * ColumnsPanel — the popover body of ColumnsMenu (also opened from the View
 * menu's `Columns · Edit…`): search, hidden-only pill, one sortable row per
 * column (drag, show, label, lock, freeze), `Show all` · `Reset to default`.
 * Keys: ↑ ↓ focus · Space show/hide · Alt+↑/↓ move · F freeze.
 */
export function ColumnsPanel({
  layout,
  groups,
  hiddenOnly = false,
  onHiddenOnlyChange,
  onRequestClose,
}) {
  const [query, setQuery] = useState("");
  const [focusId, setFocusId] = useState(null);
  const rowEls = useRef(new Map());
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  const q = query.trim().toLowerCase();
  // Group labels: the caller's `groups`, else the layout's own (the Data
  // tab's useDataGridLayout carries human labels).
  const effGroups = groups ?? layout.groups;
  const [order, setOrder] = useState(() => layout.columns.map((c) => c.id));
  const resync = useRef(false);
  useEffect(() => {
    if (!resync.current) return;
    resync.current = false;
    setOrder(layout.columns.map((c) => c.id));
  }, [layout.columns]);
  const rows = useMemo(
    () =>
      stableColumnRows(
        layout.columns,
        order,
        (effGroups ?? []).map((g) => g.id),
      ).filter(
        (c) =>
          (!hiddenOnly || c.hidden) &&
          (!q || String(c.label).toLowerCase().includes(q)),
      ),
    [layout.columns, order, effGroups, hiddenOnly, q],
  );
  const sortable = !q && !hiddenOnly;
  const groupLabel = useMemo(() => {
    const m = new Map((effGroups ?? []).map((g) => [g.id, g.label]));
    return (id) => (id ? m.get(id) ?? id : null);
  }, [effGroups]);
  const hasGroups = layout.columns.some((c) => c.group);
  // S3: warn when the frozen columns leave less than half for scrolling
  // (measured after the grid applies the layout).
  const [share, setShare] = useState(() => frozenShare());
  useEffect(() => {
    const t = setTimeout(() => setShare(frozenShare()), 250);
    return () => clearTimeout(t);
  }, [layout.prefs]);
  const frozenNote = frozenWarning(share);

  const focusRow = (id) => {
    setFocusId(id);
    rowEls.current.get(id)?.focus();
  };
  const step = (id, d) => {
    const i = rows.findIndex((r) => r.id === id);
    const next = rows[Math.max(0, Math.min(rows.length - 1, i + d))];
    if (next) focusRow(next.id);
  };
  const onRowKey = (col) => (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const d = e.key === "ArrowDown" ? 1 : -1;
      if (e.altKey) {
        if (col.locked) return;
        resync.current = true;
        layout.move(col.id, col.index + d);
        requestAnimationFrame(() => rowEls.current.get(col.id)?.focus());
      } else step(col.id, d);
    } else if (e.key === " " || e.key === "Enter") {
      e.preventDefault();
      if (!col.locked) layout.toggle(col.id);
    } else if ((e.key === "f" || e.key === "F") && !e.metaKey && !e.ctrlKey) {
      e.preventDefault();
      if (col.pinnable) layout.pin(col.id, col.pinned ? null : "left");
    } else if (e.key === "Home") {
      e.preventDefault();
      if (rows[0]) focusRow(rows[0].id);
    } else if (e.key === "End") {
      e.preventDefault();
      if (rows.length) focusRow(rows[rows.length - 1].id);
    }
  };

  const onDragEnd = ({ active, over }) => {
    if (!over || active.id === over.id) return;
    const to = layout.columns.findIndex((c) => c.id === over.id);
    resync.current = true;
    layout.move(active.id, to);
  };

  let lastGroup = null;
  return (
    <Box sx={{ width: 320, display: "flex", flexDirection: "column" }}>
      <Box sx={{ p: 1.25, pb: 0.75 }}>
        <TextField
          autoFocus
          fullWidth
          size="small"
          placeholder="Find a column…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown" && rows.length) {
              e.preventDefault();
              focusRow(rows[0].id);
            }
          }}
          inputProps={{ "aria-label": "Find a column" }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify icon="mdi:magnify" width={16} />
              </InputAdornment>
            ),
            sx: { typography: "s2" },
          }}
        />
        {frozenNote ? (
          <Typography
            typography="s3"
            data-testid="columns-frozen-warning"
            sx={{ mt: 0.75, color: "amber.700" }}
          >
            {`${frozenNote}. Unfreeze one to give the rows more room.`}
          </Typography>
        ) : null}
        {hiddenOnly && (
          <Chip
            size="small"
            label={`Hidden (${layout.hiddenCount})`}
            onDelete={() => onHiddenOnlyChange?.(false)}
            deleteIcon={
              <Iconify
                icon="mdi:close"
                width={14}
                aria-label="Show all columns"
              />
            }
            sx={{ mt: 1, typography: "s3" }}
          />
        )}
      </Box>
      <Box
        role="listbox"
        aria-label="Columns"
        sx={{ maxHeight: 360, overflowY: "auto", px: 0.75, pb: 0.75 }}
      >
        {rows.length === 0 && (
          <Typography
            typography="s2"
            color="text.secondary"
            sx={{ px: 1, py: 1.5 }}
          >
            {hiddenOnly && !q ? "No hidden columns." : "No column matches."}
          </Typography>
        )}
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={onDragEnd}
        >
          <SortableContext
            items={rows.map((r) => r.id)}
            strategy={verticalListSortingStrategy}
          >
            {rows.map((col, i) => {
              const header =
                hasGroups && (i === 0 || col.group !== lastGroup)
                  ? groupLabel(col.group)
                  : null;
              lastGroup = col.group;
              return (
                <React.Fragment key={col.id}>
                  {header && (
                    <Typography
                      typography="s3"
                      color="text.secondary"
                      sx={{ px: 1, pt: i === 0 ? 0.25 : 1, pb: 0.25 }}
                    >
                      {header}
                    </Typography>
                  )}
                  <ColumnRow
                    col={col}
                    sortable={sortable}
                    focused={focusId ? focusId === col.id : i === 0}
                    onFocusRow={() => setFocusId(col.id)}
                    layout={layout}
                    onKeyDown={onRowKey(col)}
                    rowRef={(el) => {
                      if (el) rowEls.current.set(col.id, el);
                      else rowEls.current.delete(col.id);
                    }}
                  />
                </React.Fragment>
              );
            })}
          </SortableContext>
        </DndContext>
      </Box>
      {/* IT-R1 G5 P8: a column from a nested path, from the Columns list. */}
      {layout.addFromPath ? (
        <Box
          sx={{
            px: 1,
            pt: 0.5,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Button
            size="small"
            color="primary"
            startIcon={
              <Iconify icon="mdi:table-column-plus-after" width={16} />
            }
            onClick={() => {
              onRequestClose?.();
              layout.addFromPath();
            }}
            sx={{ typography: "s2" }}
            data-testid="columns-add-from-path"
          >
            Add column from path…
          </Button>
        </Box>
      ) : null}
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        sx={{ px: 1, py: 0.75, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Button
          size="small"
          color="inherit"
          disabled={layout.hiddenCount === 0}
          onClick={layout.showAll}
          sx={{ typography: "s2" }}
        >
          Show all
        </Button>
        <Button
          size="small"
          color="inherit"
          disabled={layout.isDefault}
          onClick={() => {
            resync.current = true;
            layout.reset();
          }}
          startIcon={<Iconify icon="mdi:restore" width={16} />}
          sx={{ typography: "s2" }}
        >
          Reset to default
        </Button>
      </Stack>
    </Box>
  );
}

ColumnsPanel.propTypes = {
  layout: PropTypes.object.isRequired,
  groups: PropTypes.arrayOf(
    PropTypes.shape({ id: PropTypes.string, label: PropTypes.string }),
  ),
  hiddenOnly: PropTypes.bool,
  onHiddenOnlyChange: PropTypes.func,
  onRequestClose: PropTypes.func,
};

/**
 * ColumnsMenu — `Columns` button + `3 hidden` chip + the ColumnsPanel
 * popover (ONE-VIEW-SPEC §3.2). The chip opens the panel filtered to the
 * hidden columns. ⌘K `Columns…`, the header menu's `Columns…` and
 * `layout.openMenu()` open it too.
 *
 * Props:
 * @param {object} props.layout  from useColumnLayout
 * @param {{id: string, label: string}[]} [props.groups]  group order/labels
 * @param {"label"|"icon"} [props.buttonVariant="label"]
 * @param {string} [props.shortcutSurface]  bind `C` (keymap `columns`) on
 *   this surface while mounted (omit when the screen binds it itself)
 * @param {object} [props.sx]
 */
export default function ColumnsMenu({
  layout,
  groups,
  buttonVariant = "label",
  shortcutSurface,
  sx,
}) {
  const btnRef = useRef(null);
  const [open, setOpen] = useState(false);
  const [hiddenOnly, setHiddenOnly] = useState(false);

  useEffect(
    () =>
      registerColumnsHost(
        layout.key,
        (opts = {}) => {
          setHiddenOnly(Boolean(opts.hiddenOnly));
          setOpen(true);
        },
        2,
      ),
    [layout.key],
  );

  useShortcuts(
    shortcutSurface ?? "global",
    { columns: () => setOpen(true) },
    { enabled: Boolean(shortcutSurface) && !open, register: false },
  );

  const hidden = layout.hiddenCount;
  const label = buttonVariant === "label";
  return (
    <>
      <Stack
        ref={btnRef}
        direction="row"
        alignItems="center"
        gap={0.5}
        sx={{ flexShrink: 0, ...sx }}
      >
        <KeyHintTooltip
          title="Show, hide, freeze and reorder columns"
          action="columns"
        >
          {label ? (
            <Button
              size="small"
              variant="text"
              color="inherit"
              aria-haspopup="dialog"
              aria-expanded={open}
              onClick={() => {
                setHiddenOnly(false);
                setOpen(true);
              }}
              startIcon={<Iconify icon="mdi:view-column-outline" width={16} />}
              sx={{ typography: "s2", color: "text.secondary", minWidth: 0 }}
            >
              Columns
            </Button>
          ) : (
            <IconButton
              size="small"
              aria-label="Columns"
              aria-haspopup="dialog"
              aria-expanded={open}
              onClick={() => {
                setHiddenOnly(false);
                setOpen(true);
              }}
              sx={{ color: "text.secondary" }}
            >
              <Iconify icon="mdi:view-column-outline" width={18} />
            </IconButton>
          )}
        </KeyHintTooltip>
        {hidden > 0 && (
          <CustomTooltip show size="small" title="Show the hidden columns">
            <Chip
              size="small"
              variant="outlined"
              label={`${hidden} hidden`}
              onClick={() => {
                setHiddenOnly(true);
                setOpen(true);
              }}
              sx={{
                typography: "s3",
                color: "text.secondary",
                height: 22,
                cursor: "pointer",
              }}
            />
          </CustomTooltip>
        )}
      </Stack>
      <Popover
        open={open}
        anchorEl={btnRef.current}
        onClose={() => setOpen(false)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{ paper: { sx: { mt: 0.5 } } }}
      >
        <ColumnsPanel
          layout={layout}
          groups={groups}
          hiddenOnly={hiddenOnly}
          onHiddenOnlyChange={setHiddenOnly}
          onRequestClose={() => setOpen(false)}
        />
      </Popover>
    </>
  );
}

ColumnsMenu.propTypes = {
  layout: PropTypes.object.isRequired,
  groups: PropTypes.array,
  buttonVariant: PropTypes.oneOf(["label", "icon"]),
  shortcutSurface: PropTypes.string,
  sx: PropTypes.object,
};

export { ColumnsMenu };

/**
 * ColumnsMenuHost — the Columns popover with no button of its own, for a
 * page whose Columns doors live in places that unmount (a View popover, a
 * header menu, ⌘K) (integration C2). Mount it once per layout key, outside any
 * popover: it registers as that key's host (priority 3, above ColumnsMenu 2
 * and ViewMenu 1) and opens anchored to `getAnchor()` (e.g. the grid header).
 */
export function ColumnsMenuHost({ layout, groups, getAnchor, priority = 3 }) {
  const [anchor, setAnchor] = useState(null);
  const [hiddenOnly, setHiddenOnly] = useState(false);
  const anchorOf = useRef(getAnchor);
  anchorOf.current = getAnchor;

  useEffect(
    () =>
      registerColumnsHost(
        layout.key,
        (opts = {}) => {
          setHiddenOnly(Boolean(opts.hiddenOnly));
          setAnchor(anchorOf.current?.() ?? document.body);
        },
        priority,
      ),
    [layout.key, priority],
  );

  return (
    <Popover
      open={Boolean(anchor)}
      anchorEl={anchor}
      onClose={() => setAnchor(null)}
      anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
      transformOrigin={{ vertical: "top", horizontal: "left" }}
      slotProps={{
        paper: { sx: { mt: 0.5 }, "data-testid": "columns-menu-host" },
      }}
    >
      <ColumnsPanel
        layout={layout}
        groups={groups}
        hiddenOnly={hiddenOnly}
        onHiddenOnlyChange={setHiddenOnly}
        onRequestClose={() => setAnchor(null)}
      />
    </Popover>
  );
}

ColumnsMenuHost.propTypes = {
  layout: PropTypes.object.isRequired,
  groups: PropTypes.array,
  getAnchor: PropTypes.func,
  priority: PropTypes.number,
};
