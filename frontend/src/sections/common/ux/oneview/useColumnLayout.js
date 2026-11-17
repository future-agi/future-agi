// useColumnLayout (ONE-VIEW-SPEC §3.2): a grid's column catalog + the
// user's remembered shape (layout prefs `columns`), with Excel verbs.
//
//   const layout = useColumnLayout({ surface: "list", columns: CATALOG });
//   layout.visibleIds        // render these, in this order
//   layout.hide("owner"); layout.pin("rows", "left"); layout.reset();
//   <ColumnsMenu layout={layout} />
import { useCallback, useMemo } from "react";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";
import { useLayoutPrefs } from "./layoutPrefs";
import {
  canMoveColumnBy,
  hideColumn,
  isDefaultLayout,
  moveColumn,
  moveColumnBy,
  pinColumn,
  pinThroughColumn,
  resolveColumns,
  setColumnWidth,
  showAllColumns,
  showColumn,
  toggleColumn,
} from "./columnLayout";
import { openColumnsMenu } from "./columnsMenuHost";

/**
 * @param {{surface: string, scopeId?: string, columns: object[],
 *   commands?: boolean, commandLabel?: string}} args  `commands` (default
 *   true) registers ⌘K `Columns…` / `Reset columns` while mounted; a page
 *   with two grids names the second (`commandLabel: "Columns of All runs…"`)
 * @returns {{key: string, surface: string, catalog: object[],
 *   columns: object[], visibleIds: string[], hiddenIds: string[],
 *   hiddenCount: number, isDefault: boolean, prefs: object,
 *   hide, show, toggle, showAll, move, moveBy, canMoveBy, pin, setWidth,
 *   reset, setPrefs, openMenu}}
 */
export default function useColumnLayout({
  surface,
  scopeId,
  columns,
  commands = true,
  commandLabel = "Columns…",
}) {
  const [layoutPrefs, patch, , key] = useLayoutPrefs(surface, scopeId);
  const prefs = layoutPrefs.columns;
  const catalogKey = (columns ?? [])
    .map(
      (c) =>
        `${c.id}:${c.label}:${c.defaultVisible !== false}:${Boolean(c.locked)}:${c.defaultPinned ?? ""}:${c.group ?? ""}`,
    )
    .join("|");
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const catalog = useMemo(() => columns ?? [], [catalogKey]);

  const resolved = useMemo(
    () => resolveColumns(catalog, prefs),
    [catalog, prefs],
  );

  // Column prefs are replaced as a whole (compact output may drop keys).
  const write = useCallback(
    (fn) =>
      patch((cur) => ({ ...cur, columns: fn(cur.columns ?? {}, catalog) })),
    [patch, catalog],
  );

  const hide = useCallback(
    (id) => write((p, c) => hideColumn(p, c, id)),
    [write],
  );
  const show = useCallback(
    (id) => write((p, c) => showColumn(p, c, id)),
    [write],
  );
  const toggle = useCallback(
    (id) => write((p, c) => toggleColumn(p, c, id)),
    [write],
  );
  const showAll = useCallback(
    () => write((p, c) => showAllColumns(p, c)),
    [write],
  );
  const move = useCallback(
    (id, toIndex) => write((p, c) => moveColumn(p, c, id, toIndex)),
    [write],
  );
  const moveBy = useCallback(
    (id, delta) => write((p, c) => moveColumnBy(p, c, id, delta)),
    [write],
  );
  const canMoveBy = useCallback(
    (id, delta) => canMoveColumnBy(catalog, prefs, id, delta),
    [catalog, prefs],
  );
  const pin = useCallback(
    (id, side) => write((p, c) => pinColumn(p, c, id, side)),
    [write],
  );
  const pinThrough = useCallback(
    (id) => write((p, c) => pinThroughColumn(p, c, id)),
    [write],
  );
  const setWidth = useCallback(
    (id, px) => write((p, c) => setColumnWidth(p, c, id, px)),
    [write],
  );
  const reset = useCallback(() => write(() => ({})), [write]);
  /** Replace the column prefs (saved views, AG Grid write-back). */
  const setPrefs = useCallback((next) => write(() => next ?? {}), [write]);
  const openMenu = useCallback((opts) => openColumnsMenu(key, opts), [key]);

  const visibleIds = useMemo(
    () => resolved.filter((c) => !c.hidden).map((c) => c.id),
    [resolved],
  );
  const hiddenIds = useMemo(
    () => resolved.filter((c) => c.hidden).map((c) => c.id),
    [resolved],
  );
  const isDefault = useMemo(
    () => isDefaultLayout(catalog, prefs),
    [catalog, prefs],
  );

  useRegisterCommands(
    `columns:${key}`,
    commands
      ? [
          {
            id: `columns.${key}`,
            label: commandLabel,
            keywords: ["show", "hide", "freeze", "reorder", "columns", "pin"],
            icon: "mdi:view-column-outline",
            hint:
              hiddenIds.length > 0 ? `${hiddenIds.length} hidden` : undefined,
            run: () => openColumnsMenu(key),
          },
          {
            id: `columns.reset.${key}`,
            label: "Reset columns",
            keywords: ["columns", "default"],
            icon: "mdi:restore",
            run: reset,
            when: () => !isDefault,
          },
        ]
      : [],
    [commands, commandLabel, hiddenIds.length, isDefault],
  );

  return useMemo(
    () => ({
      key,
      surface,
      scopeId,
      catalog,
      columns: resolved,
      visibleIds,
      hiddenIds,
      hiddenCount: hiddenIds.length,
      isDefault,
      prefs: prefs ?? {},
      hide,
      show,
      toggle,
      showAll,
      move,
      moveBy,
      canMoveBy,
      pin,
      pinThrough,
      setWidth,
      reset,
      setPrefs,
      openMenu,
    }),
    [
      key,
      surface,
      scopeId,
      catalog,
      resolved,
      visibleIds,
      hiddenIds,
      isDefault,
      prefs,
      hide,
      show,
      toggle,
      showAll,
      move,
      moveBy,
      canMoveBy,
      pin,
      pinThrough,
      setWidth,
      reset,
      setPrefs,
      openMenu,
    ],
  );
}

export { useColumnLayout };
