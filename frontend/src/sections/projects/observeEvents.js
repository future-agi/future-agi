export const OBSERVE_LIST_REFRESH_EVENT = "observe-list-refresh";
export const OBSERVE_PAGE_CHANGED_EVENT = "observe-page-changed";

export const dispatchObservePageChanged = (page) => {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent(OBSERVE_PAGE_CHANGED_EVENT, { detail: { page } }),
  );
};
