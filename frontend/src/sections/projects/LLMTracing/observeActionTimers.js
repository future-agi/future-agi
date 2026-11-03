import { endActionTimer, startActionTimer } from "src/utils/actionTimer";

export const OBSERVE_FILTER_TIMER = "observe.filter.apply";
export const OBSERVE_DATE_RANGE_TIMER = "observe.date_range.change";
export const OBSERVE_TAB_TIMER = "observe.tab.open";

// `trigger` says where the filter change came from ("panel", "chip_remove",
// "chip_clear") without splitting the timing into separate groups.
export const startObserveFilterTimer = (trigger, filterCount) =>
  startActionTimer(OBSERVE_FILTER_TIMER, {
    trigger,
    ...(filterCount === undefined ? {} : { filter_count: filterCount }),
  });

export const startObserveDateRangeTimer = () =>
  startActionTimer(OBSERVE_DATE_RANGE_TIMER);

// `tab` is the fixed tab ("traces", "spans", "sessions", "users") or
// "saved_view" with its `viewType`, never a view id, so opens group together.
export const startObserveTabTimer = (tab, viewType) =>
  startActionTimer(OBSERVE_TAB_TIMER, {
    tab,
    ...(viewType ? { view_type: viewType } : {}),
  });

// Called when a list grid (traces, spans, sessions, users) has settled rows.
export const endObserveListTimers = (outcome) => {
  endActionTimer(OBSERVE_FILTER_TIMER, outcome);
  endActionTimer(OBSERVE_DATE_RANGE_TIMER, outcome);
  endActionTimer(OBSERVE_TAB_TIMER, outcome);
};
