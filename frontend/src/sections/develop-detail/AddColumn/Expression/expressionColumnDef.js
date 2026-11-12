// HO D7: an Expression column in the Data grid. Its cells are computed, so
// they are not edited in place; its header menu opens its own form
// (`Configure expression column`: edit the expression, re-run all / only
// rows whose inputs changed / only failed cells).
import { useExpressionColumnStore } from "./expressionColumnModel";

export const EXPRESSION_ORIGIN = "expression";

const originOf = (col) => col?.origin_type ?? col?.originType;

/** The grid column definition of an Expression column (others unchanged). */
export function withExpressionColumnDef(def) {
  const col = def?.col;
  if (originOf(col) !== EXPRESSION_ORIGIN) return def;
  const base = def.mainMenuItems;
  return {
    ...def,
    editable: false,
    ...(typeof base === "function"
      ? {
          mainMenuItems: (params) => [
            {
              name: "Configure expression column",
              action: () =>
                useExpressionColumnStore.getState().openFor({ editId: col.id }),
            },
            "separator",
            ...base(params),
          ],
        }
      : {}),
  };
}
