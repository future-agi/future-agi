import { useEffect, useRef } from "react";
import { endActionTimer, startActionTimer } from "src/utils/actionTimer";

export const ERROR_FEED_LIST_TIMER = "error-feed.list.update";
export const ERROR_FEED_DETAIL_TIMER = "error-feed.detail.open";
export const ERROR_FEED_TAB_TIMER = "error-feed.detail_tab.open";

// Filters, search and the time range all reset the page, so the page offset is
// ignored when deciding what changed.
export const listChangeTrigger = (previous, next) => {
  const changed = new Set(
    [...Object.keys(previous), ...Object.keys(next)].filter(
      (key) => previous[key] !== next[key],
    ),
  );
  changed.delete("offset");
  if (changed.size === 0) return "page";
  if (changed.has("search")) return "search";
  if (changed.has("sort_by") || changed.has("sort_dir")) return "sort";
  if (changed.has("time_range_days")) return "time_range";
  return "filter";
};

// Times each change to the list's request params (filter, search, sort, page,
// time range) from the change until the refreshed rows are on screen. The
// first render is not an action, so it starts nothing.
export const useErrorFeedListTimer = (params, isFetching, isError) => {
  const previousParams = useRef(params);

  useEffect(() => {
    if (previousParams.current === params) return;
    const trigger = listChangeTrigger(previousParams.current, params);
    previousParams.current = params;
    startActionTimer(ERROR_FEED_LIST_TIMER, { trigger });
  }, [params]);

  useEffect(() => {
    if (!isFetching) {
      endActionTimer(ERROR_FEED_LIST_TIMER, isError ? "error" : "ok");
    }
  }, [params, isFetching, isError]);
};

// Each detail tab calls this with its own loading state, so the timing ends
// when the tab that was just opened has its data on screen.
export const useEndErrorFeedTabTimer = (isLoading, isError = false) => {
  useEffect(() => {
    if (!isLoading) {
      endActionTimer(ERROR_FEED_TAB_TIMER, isError ? "error" : "ok");
    }
  }, [isLoading, isError]);
};
