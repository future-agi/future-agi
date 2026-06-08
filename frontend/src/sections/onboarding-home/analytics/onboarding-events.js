import { trackPostHogEvent } from "src/utils/PostHog";

export const OnboardingHomeEvents = {
  homeViewed: "onboarding_home_viewed",
  homeGoalSelected: "onboarding_home_goal_selected",
  homeGoalSaved: "onboarding_home_goal_saved",
  homeGoalSaveFailed: "onboarding_home_goal_save_failed",
  homeActionClicked: "onboarding_home_action_clicked",
  homePathClicked: "onboarding_home_path_clicked",
  recommendedActionViewed: "onboarding_recommended_action_viewed",
  recommendedActionClicked: "onboarding_recommended_action_clicked",
  activationReached: "onboarding_first_value_reached",
  activationEventRecorded: "onboarding_activation_event_recorded",
  dailyQualityHomeViewed: "daily_quality_home_viewed",
  dailyQualityTopSignalShown: "daily_quality_top_signal_shown",
  dailyQualityActionOpened: "daily_quality_action_opened",
  dailyQualityActionCompleted: "daily_quality_action_completed",
  dailyQualityActionDismissed: "daily_quality_action_dismissed",
  dailyQualityItemReviewed: "daily_quality_item_reviewed",
  dailyQualityEmptyStateViewed: "daily_quality_empty_state_viewed",
  dailyQualityDigestDestinationOpened:
    "daily_quality_digest_destination_opened",
  dailyQualityRouteFallbackUsed: "daily_quality_route_fallback_used",
  weeklyQualityReviewOpened: "weekly_quality_review_opened",
  sampleProjectOpenClicked: "sample_project_open_clicked",
  sampleProjectOpenFailed: "sample_project_open_failed",
  sampleProjectHideClicked: "sample_project_hide_clicked",
  sampleToRealSetupClicked: "sample_to_real_setup_clicked",
  testTraceSendClicked: "onboarding_test_trace_send_clicked",
  testTraceSendFailed: "onboarding_test_trace_send_failed",
  postLoginDestinationResolved: "onboarding_post_login_destination_resolved",
};

const BLOCKED_PROPERTY_KEYS = new Set([
  "email",
  "message",
  "message_text",
  "prompt",
  "prompt_text",
  "trace_payload",
  "model_response",
  "api_key",
  "provider_key",
  "secret",
]);

export const normalizeOnboardingEventProperties = (properties = {}) =>
  Object.entries(properties).reduce((result, [key, value]) => {
    if (
      value === undefined ||
      value === null ||
      BLOCKED_PROPERTY_KEYS.has(key)
    ) {
      return result;
    }
    result[key] = value;
    return result;
  }, {});

export const trackOnboardingHomeEvent = (eventName, properties = {}) => {
  if (!eventName) return false;
  trackPostHogEvent(eventName, normalizeOnboardingEventProperties(properties));
  return true;
};
