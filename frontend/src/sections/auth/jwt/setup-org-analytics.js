import { trackPostHogEvent } from "src/utils/PostHog";

export const SetupOrgEvents = {
  quickStartClicked: "setup_org_quick_start_clicked",
  quickStartProfileSaveFailed: "setup_org_quick_start_profile_save_failed",
  quickStartsViewed: "setup_org_quick_starts_viewed",
  profileSaved: "setup_org_profile_saved",
  invitesSaved: "setup_org_invites_saved",
};

const compactProperties = (properties) =>
  Object.entries(properties).reduce((result, [key, value]) => {
    if (value === undefined || value === null || value === "") {
      return result;
    }
    if (Array.isArray(value) && value.length === 0) {
      return result;
    }
    result[key] = value;
    return result;
  }, {});

const selectedGoals = (goals) =>
  Array.isArray(goals) ? goals.filter((goal) => Boolean(goal)) : [];

const newInviteMembers = (members) =>
  Array.isArray(members)
    ? members.filter((member) => !member?.disabled && member?.email?.trim())
    : [];

export const buildSetupOrgProfileSavedProperties = ({
  goals,
  provider,
  quickStartGoal,
  quickStartId,
  quickStartPrimaryPath,
  quickStartRequested,
  role,
} = {}) => {
  const goalsList = selectedGoals(goals);

  return compactProperties({
    role,
    primary_goal: goalsList[0],
    goal_count: goalsList.length,
    method: provider,
    quick_start_goal: quickStartGoal,
    quick_start_id: quickStartId,
    quick_start_primary_path: quickStartPrimaryPath,
    quick_start_requested: Boolean(quickStartRequested),
  });
};

export const buildSetupOrgInvitesSavedProperties = ({ members } = {}) => {
  const membersToTrack = newInviteMembers(members);
  const roles = [
    ...new Set(
      membersToTrack
        .map((member) => member?.organization_role)
        .filter((role) => Boolean(role)),
    ),
  ];

  return compactProperties({
    invited_member_count: membersToTrack.length,
    roles_assigned: roles,
  });
};

export const buildSetupOrgQuickStartsViewedProperties = ({
  quickStarts,
} = {}) => {
  const options = Array.isArray(quickStarts) ? quickStarts : [];
  const primaryPaths = [
    ...new Set(
      options
        .map((option) => option?.primaryPath)
        .filter((primaryPath) => Boolean(primaryPath)),
    ),
  ];

  return compactProperties({
    quick_start_count: options.length,
    featured_quick_start_count: options.filter((option) => option?.featured)
      .length,
    quick_start_ids: options
      .map((option) => option?.id)
      .filter((id) => Boolean(id)),
    quick_start_goals: options
      .map((option) => option?.goal)
      .filter((goal) => Boolean(goal)),
    quick_start_primary_paths: primaryPaths,
  });
};

export const buildSetupOrgQuickStartClickedProperties = ({
  quickStartGoal,
  quickStartId,
  quickStartPrimaryPath,
} = {}) =>
  compactProperties({
    quick_start_goal: quickStartGoal,
    quick_start_id: quickStartId,
    quick_start_primary_path: quickStartPrimaryPath,
  });

export const buildSetupOrgQuickStartProfileSaveFailedProperties = ({
  quickStartGoal,
  quickStartId,
  quickStartPrimaryPath,
  reason,
  status,
} = {}) =>
  compactProperties({
    quick_start_goal: quickStartGoal,
    quick_start_id: quickStartId,
    quick_start_primary_path: quickStartPrimaryPath,
    reason,
    status,
  });

export const trackSetupOrgQuickStartsViewed = (properties) => {
  trackPostHogEvent(
    SetupOrgEvents.quickStartsViewed,
    buildSetupOrgQuickStartsViewedProperties(properties),
  );
};

export const trackSetupOrgQuickStartClicked = (properties) => {
  trackPostHogEvent(
    SetupOrgEvents.quickStartClicked,
    buildSetupOrgQuickStartClickedProperties(properties),
  );
};

export const trackSetupOrgQuickStartProfileSaveFailed = (properties) => {
  trackPostHogEvent(
    SetupOrgEvents.quickStartProfileSaveFailed,
    buildSetupOrgQuickStartProfileSaveFailedProperties(properties),
  );
};

export const trackSetupOrgProfileSaved = (properties) => {
  trackPostHogEvent(
    SetupOrgEvents.profileSaved,
    buildSetupOrgProfileSavedProperties(properties),
  );
};

export const trackSetupOrgInvitesSaved = (properties) => {
  trackPostHogEvent(
    SetupOrgEvents.invitesSaved,
    buildSetupOrgInvitesSavedProperties(properties),
  );
};
