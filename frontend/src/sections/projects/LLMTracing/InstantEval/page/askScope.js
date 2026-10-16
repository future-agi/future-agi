// A new question started on the Ask page, not handed over from a view, asks
// about the past week's rows: the hour the week began up to the next
// midnight, as the views' own "Past 7D" window reads.
const DAY_MS = 24 * 3600 * 1000;

export function lastWeekScope(unit, now = new Date()) {
  const start = new Date(now.getTime() - 7 * DAY_MS);
  start.setMinutes(0, 0, 0);
  const end = new Date(now);
  end.setHours(24, 0, 0, 0);
  return {
    unit,
    label: "Past 7 days",
    filters: [
      {
        column_id: "created_at",
        filter_config: {
          filter_type: "datetime",
          filter_op: "between",
          filter_value: [start.toISOString(), end.toISOString()],
        },
      },
    ],
  };
}
