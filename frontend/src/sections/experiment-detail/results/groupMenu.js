// The ⋮ menu of a results column group (PRODUCT-SPEC §3.10.7):
// Aggregate ▸ · Sort by biggest regression / improvement · Show/Hide reasons
// · Direction (read-only) · Eval details · Re-run this eval · then the Excel
// header items (one view: Freeze · Move left · Move right · Hide column ·
// Columns… · Reset columns). Pure: menu item objects for ResultGroupHeader.
import { reducerLabel } from "src/sections/common/Analysis/trialReducers";
// HO R1 (A-H4): custom column groups and `+ Add column…`.
import {
  addColumnItem,
  customGroupMenuItems,
} from "src/sections/common/CustomColumns/resultsCustomColumns";
import { openCustomColumnDoor } from "src/sections/common/CustomColumns/customColumnDoor";
import { customColumnsAvailable } from "src/sections/common/CustomColumns/useCustomColumns";

/**
 * G's header-menu items (`headerMenuItems`) in this menu's shape: dividers
 * become the next item's `divider`, reasons the disabled rows' second line.
 */
export function fromHeaderItems(items) {
  const out = [];
  let divider = false;
  (items ?? []).forEach((i) => {
    if (i.divider) {
      divider = true;
      return;
    }
    out.push({
      label: i.label,
      icon: i.icon,
      disabled: Boolean(i.disabled),
      secondary: i.disabled ? i.reason : undefined,
      selected: Boolean(i.checked),
      onClick: () => i.run?.(),
      divider,
    });
    divider = false;
  });
  if (out[0]) out[0] = { ...out[0], divider: true };
  return out;
}

const AGG_ITEMS = [
  { value: "avg", label: "Average" },
  { value: "median", label: "Median" },
  // W1-4: the share of answered rows the judge said N/A on (an eval whose
  // judge can answer N/A on a run in view: R3 `answers_na`).
  { value: "na_rate", label: "N/A rate", naOnly: true },
];

/**
 * W1-8: the trial reducers of an eval whose runs have k > 1 tries per row:
 * Most common · Best · pass@k · pass^k · At least n of k (n = 1 … k−1;
 * at least k is pass^k). Empty for one try per row or a human column.
 */
export function trialAggItems(group, def) {
  const k = Number(def?.trials_k) || 1;
  if (group?.kind !== "eval" || k < 2) return [];
  // Review M6: on a score eval Most common reads each try's verdict at the
  // pass mark (a share of rows), so the menu says so.
  const score =
    def?.output_type && !["pass_fail", "thumbs"].includes(def.output_type);
  return [
    {
      value: "mode",
      label: score
        ? `${reducerLabel("mode", k)} verdict`
        : reducerLabel("mode", k),
    },
    { value: "max", label: reducerLabel("max", k) },
    { value: "pass_at_k", label: reducerLabel("pass_at_k", k) },
    { value: "pass_hat_k", label: reducerLabel("pass_hat_k", k) },
    ...Array.from({ length: k - 2 }, (_, i) => ({
      value: `at_least:${i + 2}`,
      label: reducerLabel({ kind: "at_least", n: i + 2 }, k),
    })),
  ];
}

/**
 * @param {{key: string, kind: string, def?: object}} group
 * @param {object} o  { agg, setAgg, firstCandidate, multiRun, showReasons,
 *   toggleReasons, hide, sortBy, openEvalDetails, rerunEval, readOnly, mode,
 *   layoutItems? } — `layoutItems` (fromHeaderItems) replace `Hide column`
 */
export function buildGroupMenu(group, o) {
  const items = [];
  const def = group.def;
  const metricLike = group.kind === "eval" || group.kind === "human";
  const columnDoor = {
    ...o,
    openColumn:
      o.openColumn ?? (customColumnsAvailable() ? openCustomColumnDoor : null),
  };
  if (group.kind === "custom") {
    items.push(...customGroupMenuItems(group, columnDoor));
    items.push(...addColumnItem(columnDoor));
    if (o.layoutItems?.length) items.push(...o.layoutItems);
    return items;
  }

  if (metricLike) {
    items.push({ heading: true, label: "Aggregate" });
    // W1-8: a run that set its own reducer is read by it (the default).
    const own = Object.values(def?.run_reducers ?? {});
    if (own.length)
      items.push({
        label: `As each run sets it (${[...new Set(own.map((r) => reducerLabel(r, def?.trials_k)))].join(", ")})`,
        selected: o.agg === "run",
        onClick: () => o.setAgg("run"),
      });
    AGG_ITEMS.filter(
      (a) => !a.naOnly || (group.kind === "eval" && def?.answers_na),
    ).forEach((a) =>
      items.push({
        label: a.label,
        selected:
          o.agg === a.value ||
          (a.value === "avg" && o.agg === "run" && !own.length),
        disabled:
          a.value === "pass" &&
          def?.output_type !== "pass_fail" &&
          def?.pass_threshold == null,
        onClick: () => o.setAgg(a.value),
      }),
    );
    // W1-8: how each row's tries become one value, offered when a run in
    // view has more than one try per row (R3 `trials_k`).
    trialAggItems(group, def).forEach((a) =>
      items.push({
        label: a.label,
        selected: o.agg === a.value,
        onClick: () => o.setAgg(a.value),
      }),
    );
  }

  if (group.kind !== "preference" && group.kind !== "output") {
    const run = o.firstCandidate;
    items.push({
      label: "Sort by biggest regression",
      icon: "material-symbols:trending-down-rounded",
      disabled: !o.multiRun || !run,
      onClick: () => o.sortBy(`delta:${group.key}:${run}:asc`),
      divider: metricLike,
    });
    items.push({
      label: "Sort by biggest improvement",
      icon: "material-symbols:trending-up-rounded",
      disabled: !o.multiRun || !run,
      onClick: () => o.sortBy(`delta:${group.key}:${run}:desc`),
    });
  }

  if (group.kind === "eval") {
    items.push({
      label: o.showReasons ? "Hide reasons" : "Show reasons",
      icon: o.showReasons
        ? "mdi:text-box-remove-outline"
        : "mdi:text-box-outline",
      onClick: o.toggleReasons,
      divider: true,
    });
  }

  if (def && def.higher_is_better != null) {
    items.push({
      label: def.higher_is_better ? "Higher is better" : "Lower is better",
      secondary: "Direction is set on the eval",
      icon: def.higher_is_better
        ? "material-symbols:arrow-upward-rounded"
        : "material-symbols:arrow-downward-rounded",
      disabled: true,
      divider: group.kind !== "eval",
    });
  }

  if (group.kind === "eval" && o.openEvalDetails) {
    items.push({
      label: "Eval details",
      icon: "mdi:information-outline",
      onClick: () => o.openEvalDetails(group),
    });
  }
  if (group.kind === "eval" && o.rerunEval && !o.readOnly) {
    items.push({
      label: "Re-score this eval…",
      icon: "mdi:refresh",
      onClick: () => o.rerunEval(group),
    });
  }

  items.push(...addColumnItem(columnDoor));
  if (o.layoutItems?.length) items.push(...o.layoutItems);
  else
    items.push({
      label: "Hide column",
      icon: "mdi:eye-off-outline",
      onClick: o.hide,
      divider: true,
    });
  return items;
}
