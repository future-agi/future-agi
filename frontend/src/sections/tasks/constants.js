// Labels rendered by DateTimeRangePicker. The picker reports the selection
// back as its label string, so these are the values we compare against.
export const DATE_OPTION = {
  THIRTY_MINS: "30 mins",
  SIX_HOURS: "6 hrs",
  TODAY: "Today",
  YESTERDAY: "Yesterday",
  SEVEN_DAYS: "7D",
  THIRTY_DAYS: "30D",
  THREE_MONTHS: "3M",
  SIX_MONTHS: "6M",
  TWELVE_MONTHS: "12M",
  CUSTOM: "Custom",
};

// `period` values accepted by GET /tracer/eval-task/get_usage/ — mirrors
// UsagePeriod in tracer/constants/eval_task_usage.py. CUSTOM and ALL are
// response-only: the backend reports them through period_requested /
// period_used and never accepts them as input.
export const USAGE_PERIOD = {
  THIRTY_MINUTES: "30m",
  SIX_HOURS: "6h",
  ONE_DAY: "1d",
  SEVEN_DAYS: "7d",
  THIRTY_DAYS: "30d",
  NINETY_DAYS: "90d",
  ONE_EIGHTY_DAYS: "180d",
  ONE_YEAR: "365d",
  CUSTOM: "custom",
  ALL: "all",
};

export const DEFAULT_USAGE_PERIOD = USAGE_PERIOD.THIRTY_DAYS;

// Tasks may run over months, so this extends the eval-usage map with the "6M"
// and "12M" picker options. A missing entry silently falls back to 30d, which
// is why every picker label is listed. "Custom" resolves through start_date /
// end_date instead, so its period is only a placeholder.
export const DATE_OPTION_TO_PERIOD = {
  [DATE_OPTION.THIRTY_MINS]: USAGE_PERIOD.THIRTY_MINUTES,
  [DATE_OPTION.SIX_HOURS]: USAGE_PERIOD.SIX_HOURS,
  [DATE_OPTION.TODAY]: USAGE_PERIOD.ONE_DAY,
  [DATE_OPTION.YESTERDAY]: USAGE_PERIOD.ONE_DAY,
  [DATE_OPTION.SEVEN_DAYS]: USAGE_PERIOD.SEVEN_DAYS,
  [DATE_OPTION.THIRTY_DAYS]: USAGE_PERIOD.THIRTY_DAYS,
  [DATE_OPTION.THREE_MONTHS]: USAGE_PERIOD.NINETY_DAYS,
  [DATE_OPTION.SIX_MONTHS]: USAGE_PERIOD.ONE_EIGHTY_DAYS,
  [DATE_OPTION.TWELVE_MONTHS]: USAGE_PERIOD.ONE_YEAR,
  [DATE_OPTION.CUSTOM]: DEFAULT_USAGE_PERIOD,
};
