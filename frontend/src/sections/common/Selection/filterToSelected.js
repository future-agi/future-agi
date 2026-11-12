// W5-3 / W5-7 (C-H1): `Filter to selected` on the leaderboard selection bar.
// The leaderboard's own filter rows (ComplexFilter `{column_id,
// filter_config}`) gain a `run_id` option filter holding the selected run
// ids, so the narrowed view is a filter like any other (the Filter panel
// shows it, Clear filters removes it, the URL keeps it).

export const RUN_FILTER_FIELD = "run_id";

const uniqueIds = (ids) => [...new Set((ids ?? []).filter(Boolean))];

/** The leaderboard filter row for exactly these runs. */
export function runIdsFilter(ids) {
  return {
    column_id: RUN_FILTER_FIELD,
    filter_config: {
      filter_type: "option",
      filter_op: "equals",
      filter_value: uniqueIds(ids),
    },
  };
}

/** The view's filters with the run filter replaced by these runs. */
export function withRunFilter(filters, ids) {
  const keep = (filters ?? []).filter((f) => f?.column_id !== RUN_FILTER_FIELD);
  const runIds = uniqueIds(ids);
  return runIds.length ? [...keep, runIdsFilter(runIds)] : keep;
}

/** Run filter options: `#2A Support Reply V4` per run, newest label order. */
export function runFilterOptions(runs) {
  return (runs ?? [])
    .filter((r) => r?.run_id)
    .map((r) => ({
      value: r.run_id,
      label: [r.label, r.name].filter(Boolean).join(" ") || r.run_id,
    }))
    .sort((a, b) =>
      a.label.localeCompare(b.label, undefined, { numeric: true }),
    );
}
