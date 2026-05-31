import { paths } from "src/routes/paths";

export const SETUP_COMPLETION_SOURCE = "setup_org";
export const SETUP_COMPLETION_HANDOFF_STORAGE_KEY =
  "futureagi.setup_completion_handoff";

const appendIfPresent = (params, key, value) => {
  if (value !== undefined && value !== null && value !== "") {
    params.set(key, value);
  }
};

export const setupCompletionHomeHref = ({
  quickStartGoal,
  quickStartId,
  quickStartPrimaryPath,
} = {}) => {
  const params = new URLSearchParams({ source: SETUP_COMPLETION_SOURCE });
  appendIfPresent(params, "quick_start_id", quickStartId);
  appendIfPresent(params, "quick_start_goal", quickStartGoal);
  appendIfPresent(params, "quick_start_primary_path", quickStartPrimaryPath);

  return `${paths.dashboard.home}?${params.toString()}`;
};

export const resolveSetupCompletionHref = (quickStartOption) =>
  setupCompletionHomeHref({
    quickStartGoal: quickStartOption?.goal,
    quickStartId: quickStartOption?.id,
    quickStartPrimaryPath: quickStartOption?.primaryPath,
  });

export const persistSetupCompletionReturnTo = (href) => {
  if (
    !href ||
    typeof href !== "string" ||
    !href.startsWith("/") ||
    href.startsWith("//") ||
    typeof window === "undefined"
  ) {
    return false;
  }

  try {
    window.localStorage.setItem("redirectUrl", href);
    window.sessionStorage?.setItem(SETUP_COMPLETION_HANDOFF_STORAGE_KEY, href);
    return true;
  } catch {
    return false;
  }
};

export const isSetupCompletionHandoff = (href) => {
  if (
    !href ||
    typeof href !== "string" ||
    typeof window === "undefined" ||
    !href.startsWith("/") ||
    href.startsWith("//")
  ) {
    return false;
  }

  try {
    const storedHref = window.sessionStorage?.getItem(
      SETUP_COMPLETION_HANDOFF_STORAGE_KEY,
    );
    if (!storedHref) return false;

    const storedUrl = new URL(storedHref, window.location.origin);
    const url = new URL(href, window.location.origin);
    const quickStartId = url.searchParams.get("quick_start_id");
    const quickStartGoal = url.searchParams.get("quick_start_goal");
    const quickStartPrimaryPath = url.searchParams.get(
      "quick_start_primary_path",
    );
    const storedQuickStartGoal = storedUrl.searchParams.get("quick_start_goal");
    const storedQuickStartPrimaryPath = storedUrl.searchParams.get(
      "quick_start_primary_path",
    );
    return (
      storedUrl.pathname === url.pathname &&
      url.pathname === paths.dashboard.home &&
      storedUrl.searchParams.get("source") === SETUP_COMPLETION_SOURCE &&
      Boolean(quickStartId) &&
      storedUrl.searchParams.get("quick_start_id") === quickStartId &&
      (!quickStartGoal || storedQuickStartGoal === quickStartGoal) &&
      (!quickStartPrimaryPath ||
        storedQuickStartPrimaryPath === quickStartPrimaryPath)
    );
  } catch {
    return false;
  }
};

export const shouldShowInviteStepAfterProfileSave = ({
  isOwner,
  quickStartRequested,
} = {}) => Boolean(isOwner && !quickStartRequested);
