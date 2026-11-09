import React, { useCallback, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import { ItemsMenu } from "./MenuItems";
import { isMenuKey, menuTargetOf } from "./HeaderMenu";

const LONG_PRESS_MS = 500;

/**
 * useContextMenu — right-click, long-press (touch) and Shift+F10 / the
 * ContextMenu key on rows, cells, steps, lanes and chips (ONE-VIEW-SPEC
 * §3.8). Opens at the pointer (or under the element from the keyboard).
 *
 *   const cm = useContextMenu();
 *   <Row {...cm.bind(() => [...rowActions, copyLinkItem(href)])} />
 *   <ContextMenu {...cm.menuProps} />
 *
 * @returns {{open(target, items), close(), isOpen: boolean,
 *   bind(items | () => items): object, menuProps: object}}
 */
export function useContextMenu() {
  const [state, setState] = useState(null); // {items, anchorPosition}
  const pressTimer = useRef(null);

  const open = useCallback((target, items) => {
    const list = typeof items === "function" ? items() : items;
    if (!list?.length) return;
    setState({ items: list, ...menuTargetOf(target) });
  }, []);
  const close = useCallback(() => setState(null), []);

  const bind = useCallback(
    (items) => ({
      onContextMenu: (e) => {
        e.preventDefault();
        e.stopPropagation();
        open(e, items);
      },
      onKeyDown: (e) => {
        if (!isMenuKey(e)) return;
        e.preventDefault();
        e.stopPropagation();
        open(e.currentTarget, items);
      },
      onTouchStart: (e) => {
        const t = e.touches?.[0];
        clearTimeout(pressTimer.current);
        if (!t) return;
        const at = { x: t.clientX, y: t.clientY };
        pressTimer.current = setTimeout(() => open(at, items), LONG_PRESS_MS);
      },
      onTouchEnd: () => clearTimeout(pressTimer.current),
      onTouchMove: () => clearTimeout(pressTimer.current),
    }),
    [open],
  );

  const menuProps = useMemo(
    () => ({
      open: Boolean(state),
      onClose: close,
      anchorPosition: state?.anchorPosition,
      items: state?.items ?? [],
    }),
    [state, close],
  );

  return { open, close, isOpen: Boolean(state), bind, menuProps };
}

/**
 * ContextMenu — renders `useContextMenu().menuProps`. Items `{id, label,
 * icon?, shortcut?, run?, href?, target?, disabled?, reason?, danger?,
 * divider?}`: key caps right-aligned, disabled rows say why, danger items
 * last after a divider. An item with `href` navigates (`target: "_blank"`
 * opens a new tab).
 */
export default function ContextMenu({ open, onClose, anchorPosition, items }) {
  const navigate = useNavigate();
  return (
    <ItemsMenu
      open={open}
      onClose={onClose}
      anchorPosition={anchorPosition}
      items={items}
      data-fa-context-menu=""
      onRun={(item, e) => {
        if (item.run) item.run(e);
        else if (item.href && item.target === "_blank")
          window.open(item.href, "_blank", "noopener,noreferrer");
        else if (item.href) navigate(item.href);
      }}
    />
  );
}

ContextMenu.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  anchorPosition: PropTypes.object,
  items: PropTypes.array,
};

export { ContextMenu };
