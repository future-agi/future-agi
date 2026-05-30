import { paths } from "src/routes/paths";

export const SETUP_COMPLETION_SOURCE = "setup_org";

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

export const shouldShowInviteStepAfterProfileSave = ({
  isOwner,
  quickStartRequested,
} = {}) => Boolean(isOwner && !quickStartRequested);
