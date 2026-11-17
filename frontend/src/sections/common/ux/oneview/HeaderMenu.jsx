import React, { useCallback, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, IconButton, Popover, Stack, TableCell } from "@mui/material";
import Iconify from "src/components/iconify";
import { ItemsMenu } from "./MenuItems";
import { ColumnsPanel } from "./ColumnsMenu";
import { headerMenuItems } from "./headerMenuItems";

/** Where a menu opens: the pointer for a mouse event, else the element. */
export function menuTargetOf(target) {
  if (!target) return {};
  if (typeof target.clientX === "number" && target.clientX > 0)
    return { anchorPosition: { top: target.clientY, left: target.clientX } };
  if (typeof target.x === "number" && typeof target.y === "number")
    return { anchorPosition: { top: target.y, left: target.x } };
  const el = target.currentTarget ?? target;
  if (el?.getBoundingClientRect) {
    const r = el.getBoundingClientRect();
    return { anchorPosition: { top: r.bottom, left: r.left } };
  }
  return {};
}

/** Shift+F10 or the ContextMenu key. */
export const isMenuKey = (e) =>
  e.key === "ContextMenu" || (e.shiftKey && e.key === "F10");

/**
 * useHeaderMenu — state for the Excel header menu of one grid.
 *
 *   const hm = useHeaderMenu(layout, { sort, onSort, group, onGroup });
 *   <th {...hm.headerProps("rows")}>Rows <HeaderMenuButton … /></th>
 *   <HeaderMenu {...hm.menuProps} />
 *
 * @returns {{open(columnId, target), close(), isOpen: boolean,
 *   columnId: string|null, itemsFor(columnId): object[],
 *   headerProps(columnId): object, menuProps: object}}
 */
export function useHeaderMenu(layout, opts = {}) {
  const [state, setState] = useState(null); // {columnId, anchorPosition}
  const [columnsAt, setColumnsAt] = useState(null); // anchorPosition
  const { sort, onSort, group, onGroup } = opts;

  const close = useCallback(() => setState(null), []);
  const open = useCallback((columnId, target) => {
    setState({ columnId, ...menuTargetOf(target) });
  }, []);

  const itemsFor = useCallback(
    (columnId) =>
      headerMenuItems(layout, columnId, {
        sort,
        onSort,
        group,
        onGroup,
        onColumns: () => {
          // A ColumnsMenu / ViewMenu on the page opens; else our own panel.
          const at = state?.anchorPosition ?? null;
          if (!layout.openMenu?.()) setColumnsAt(at ?? { top: 120, left: 320 });
        },
      }),
    [layout, sort, onSort, group, onGroup, state],
  );

  const headerProps = useCallback(
    (columnId) => ({
      "data-fa-col": columnId,
      onContextMenu: (e) => {
        e.preventDefault();
        open(columnId, e);
      },
      onKeyDown: (e) => {
        if (isMenuKey(e)) {
          e.preventDefault();
          e.stopPropagation();
          open(columnId, e.currentTarget);
        }
      },
    }),
    [open],
  );

  const menuProps = useMemo(
    () => ({
      open: Boolean(state),
      onClose: close,
      anchorPosition: state?.anchorPosition,
      items: state ? itemsFor(state.columnId) : [],
      columnsPanel: {
        open: Boolean(columnsAt),
        anchorPosition: columnsAt ?? undefined,
        onClose: () => setColumnsAt(null),
        layout,
      },
    }),
    [state, close, itemsFor, columnsAt, layout],
  );

  return {
    open,
    close,
    isOpen: Boolean(state),
    columnId: state?.columnId ?? null,
    itemsFor,
    headerProps,
    menuProps,
  };
}

/**
 * HeaderMenu — renders `useHeaderMenu().menuProps`: the item menu and, when
 * no ColumnsMenu / ViewMenu is on the page, its own Columns panel.
 */
export default function HeaderMenu({
  open,
  onClose,
  anchorPosition,
  items,
  columnsPanel,
}) {
  return (
    <>
      <ItemsMenu
        open={open}
        onClose={onClose}
        anchorPosition={anchorPosition}
        items={items}
        minWidth={232}
        data-fa-header-menu=""
      />
      {columnsPanel && (
        <Popover
          open={columnsPanel.open}
          onClose={columnsPanel.onClose}
          anchorReference="anchorPosition"
          anchorPosition={columnsPanel.anchorPosition}
        >
          {columnsPanel.open && <ColumnsPanel layout={columnsPanel.layout} />}
        </Popover>
      )}
    </>
  );
}

HeaderMenu.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  anchorPosition: PropTypes.object,
  items: PropTypes.array,
  columnsPanel: PropTypes.object,
};

/**
 * HeaderMenuButton — the header's `⋮`, visible on header hover or focus
 * (OneViewStyles). Never sorts or drags the column it sits in.
 */
export function HeaderMenuButton({ label, open = false, onOpen, sx }) {
  const stop = (e) => e.stopPropagation();
  return (
    <IconButton
      size="small"
      className="fa-header-menu-btn"
      aria-label={label ? `Column menu: ${label}` : "Column menu"}
      aria-haspopup="menu"
      aria-expanded={open}
      onMouseDown={stop}
      onPointerDown={stop}
      onClick={(e) => {
        e.stopPropagation();
        e.preventDefault();
        onOpen?.(e.currentTarget);
      }}
      sx={{ p: 0.25, ml: 0.25, color: "text.secondary", flexShrink: 0, ...sx }}
    >
      <Iconify icon="mdi:dots-vertical" width={16} />
    </IconButton>
  );
}

HeaderMenuButton.propTypes = {
  label: PropTypes.string,
  open: PropTypes.bool,
  onOpen: PropTypes.func,
  sx: PropTypes.object,
};

/**
 * HeaderMenuCell — a MUI `TableCell` header with the Excel menu
 * (right-click, `⋮`, Shift+F10). Render the body with `layout.visibleIds`.
 *
 * Props: `{column (id), layout, sort?, onSort?, group?, onGroup?, children,
 * sx?, ...TableCellProps}`.
 */
export function HeaderMenuCell({
  column,
  layout,
  sort,
  onSort,
  group,
  onGroup,
  children,
  sx,
  ...cellProps
}) {
  const hm = useHeaderMenu(layout, { sort, onSort, group, onGroup });
  const def = layout.columns.find((c) => c.id === column);
  // The menu renders beside the cell (portal), so its events never bubble
  // into the cell's own right-click / key handlers.
  return (
    <>
      <TableCell
        {...cellProps}
        {...hm.headerProps(column)}
        tabIndex={0}
        className={["fa-header-cell", cellProps.className]
          .filter(Boolean)
          .join(" ")}
        sx={{
          "&:focus-visible": {
            outline: "2px solid",
            outlineColor: "primary.main",
            outlineOffset: -2,
          },
          ...sx,
        }}
      >
        <Stack direction="row" alignItems="center" sx={{ minWidth: 0 }}>
          <Box sx={{ flex: 1, minWidth: 0 }}>{children}</Box>
          <HeaderMenuButton
            label={def?.label}
            open={hm.isOpen}
            onOpen={(el) => hm.open(column, el)}
          />
        </Stack>
      </TableCell>
      <HeaderMenu {...hm.menuProps} />
    </>
  );
}

HeaderMenuCell.propTypes = {
  column: PropTypes.string.isRequired,
  layout: PropTypes.object.isRequired,
  sort: PropTypes.object,
  onSort: PropTypes.func,
  group: PropTypes.string,
  onGroup: PropTypes.func,
  children: PropTypes.node,
  sx: PropTypes.object,
};

export { HeaderMenu };
