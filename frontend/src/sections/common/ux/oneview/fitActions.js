// Priority-plus fitting for ActionBar (ONE-VIEW-SPEC §3.6). Pure.
//
// Actions show in their declared order while they fit. When the bar is too
// narrow, the LOWEST priority goes first (priority 4 before 3 …, and within
// a priority the right-most first): it drops its label (icon-only) when it
// allows that (`hideLabelBelow` + an icon), and overflows into `More ▾` when
// that is not enough. Priority 1 and `variant: "primary"` never overflow.
// `hideLabelBelow: px` also forces icon-only whenever the bar is narrower.
// `inMore: true` keeps an action in `More ▾` at every width (IT-R4 G5 P6:
// a toolbar within budget keeps only its everyday actions inline).

export const ACTION_GAP = 4;
export const MORE_WIDTH = 72;

const prio = (a) => a.priority ?? 2;
const pinned = (a) => prio(a) === 1 || a.variant === "primary";
const compactable = (a) =>
  Boolean(a.icon) &&
  a.hideLabelBelow != null &&
  !a.render &&
  a.variant !== "icon";

/**
 * @param {{id: string, priority?: 1|2|3|4, icon?: string,
 *   hideLabelBelow?: number, variant?: string, render?: Function}[]} actions
 * @param {Object<string, {full: number, icon?: number}>} widths  measured px
 * @param {number} available  bar width in px
 * @param {{gap?: number, moreWidth?: number}} [opts]
 * @returns {{shown: string[], compact: string[], overflow: string[]}}
 *   `shown` and `overflow` keep the declared order; `compact` ⊂ `shown`
 */
export function fitActions(actions, widths, available, opts = {}) {
  const gap = opts.gap ?? ACTION_GAP;
  const moreWidth = opts.moreWidth ?? MORE_WIDTH;
  const list = (actions ?? []).filter(Boolean);
  const state = new Map(
    list.map((a) => [
      a.id,
      a.inMore && !pinned(a)
        ? "over"
        : compactable(a) && available < a.hideLabelBelow
          ? "icon"
          : "full",
    ]),
  );
  const widthOf = (a) => {
    const w = widths?.[a.id] ?? {};
    const full = w.full ?? 0;
    return state.get(a.id) === "icon" ? w.icon ?? full : full;
  };
  const total = () => {
    const shown = list.filter((a) => state.get(a.id) !== "over");
    const sum = shown.reduce((s, a) => s + widthOf(a), 0);
    const gaps = Math.max(0, shown.length - 1) * gap;
    const over = shown.length < list.length;
    return sum + gaps + (over ? (shown.length ? gap : 0) + moreWidth : 0);
  };
  const fits = () => total() <= available;

  if (!fits()) {
    const order = list
      .map((a, i) => ({ a, i }))
      .sort((x, y) => prio(y.a) - prio(x.a) || y.i - x.i)
      .map((x) => x.a);
    for (const a of order) {
      if (fits()) break;
      if (state.get(a.id) === "full" && compactable(a)) {
        state.set(a.id, "icon");
        if (fits()) break;
      }
      if (pinned(a)) continue;
      state.set(a.id, "over");
    }
  }

  return {
    shown: list.filter((a) => state.get(a.id) !== "over").map((a) => a.id),
    compact: list.filter((a) => state.get(a.id) === "icon").map((a) => a.id),
    overflow: list.filter((a) => state.get(a.id) === "over").map((a) => a.id),
  };
}

/** More than one `variant: "primary"` (P3: one primary per region). */
export const primaryCount = (actions) =>
  (actions ?? []).filter((a) => a?.variant === "primary").length;
