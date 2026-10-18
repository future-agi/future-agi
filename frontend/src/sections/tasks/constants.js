// Labels rendered by DateTimeRangePicker. The picker reports the selection
// back as its label string, so these are the values we compare against.
export const DATE_OPTION = {
  THIRTY_MINS: "30 mins",
  ONE_HOUR: "1 hr",
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
  ONE_HOUR: "1h",
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

// DATE_OPTION_TO_PERIOD lives in src/sections/evals/Helpers/evalUsageColumns —
// shared with EvalUsageTab so both usage tabs resolve the same picker labels
// to the same period tokens.
