// Hover selection (ONE-VIEW-SPEC §3.10): a grid's checkbox column shows on
// row hover, on row keyboard focus, and on every row once any row is
// selected. Selection keys (X, Shift+click, ⌘A) work regardless; this only
// changes what is painted. The CSS lives in OneViewStyles (mounted once).
//
// AG Grid:
//   const hover = useHoverSelection(gridRef);
//   <Box className={hover.className}>
//     <AgGridReact ref={gridRef} onSelectionChanged={(e) => {
//       hover.onSelectionChanged(e); mine(e); }} … />
//   </Box>
// TanStack / MUI DataTable: `<DataTable selectOnHover … />`.
import { useCallback, useEffect, useState } from "react";

export const HOVER_SELECT_CLASS = "fa-hover-select";
export const HAS_SELECTION_CLASS = "fa-has-selection";

/** `fa-hover-select` + `fa-has-selection` while anything is selected. */
export const hoverSelectClass = (hasSelection) =>
  hasSelection
    ? `${HOVER_SELECT_CLASS} ${HAS_SELECTION_CLASS}`
    : HOVER_SELECT_CLASS;

const countOf = (api) => {
  try {
    return api?.getSelectedNodes?.().length ?? 0;
  } catch {
    return 0;
  }
};

/**
 * @param {{current: {api?: object}}} gridRef  an AgGridReact ref
 * @returns {{className: string, hasSelection: boolean,
 *   onSelectionChanged: (event) => void}}
 */
export default function useHoverSelection(gridRef) {
  const [hasSelection, setHas] = useState(false);
  const onSelectionChanged = useCallback((event) => {
    setHas(countOf(event?.api) > 0);
  }, []);

  // Also listen directly once the grid api exists, so a screen that forgets
  // to forward onSelectionChanged still gets the class.
  useEffect(() => {
    let api = null;
    let tries = 0;
    let timer = null;
    const listener = (e) => onSelectionChanged(e);
    const attach = () => {
      api = gridRef?.current?.api ?? null;
      if (api?.addEventListener) {
        api.addEventListener("selectionChanged", listener);
        setHas(countOf(api) > 0);
        return;
      }
      tries += 1;
      if (tries < 40) timer = setTimeout(attach, 50);
    };
    attach();
    return () => {
      clearTimeout(timer);
      try {
        if (api && !api.isDestroyed?.())
          api.removeEventListener?.("selectionChanged", listener);
      } catch {
        /* grid already gone */
      }
    };
  }, [gridRef, onSelectionChanged]);

  return {
    className: hoverSelectClass(hasSelection),
    hasSelection,
    onSelectionChanged,
  };
}

export { useHoverSelection };
