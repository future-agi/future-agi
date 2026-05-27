import { beforeEach, describe, expect, it, vi } from "vitest";
import { trackPostHogEvent } from "src/utils/PostHog";
import {
  OnboardingHomeEvents,
  normalizeOnboardingEventProperties,
  trackOnboardingHomeEvent,
} from "../analytics/onboarding-events";

vi.mock("src/utils/PostHog", () => ({
  trackPostHogEvent: vi.fn(),
}));

describe("onboarding home events", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("tracks explicit onboarding events through PostHog", () => {
    const tracked = trackOnboardingHomeEvent(
      OnboardingHomeEvents.homeActionClicked,
      {
        action_id: "create_observe_project",
        prompt: "blocked",
        provider_key: "blocked",
        stage: "connect_observability",
      },
    );

    expect(tracked).toBe(true);
    expect(trackPostHogEvent).toHaveBeenCalledWith(
      "onboarding_home_action_clicked",
      {
        action_id: "create_observe_project",
        stage: "connect_observability",
      },
    );
  });

  it("exposes the post-login routing analytics event", () => {
    expect(OnboardingHomeEvents.postLoginDestinationResolved).toBe(
      "onboarding_post_login_destination_resolved",
    );
  });

  it("exposes canonical dashboard events for launch measurement", () => {
    expect(OnboardingHomeEvents.homeViewed).toBe("onboarding_home_viewed");
    expect(OnboardingHomeEvents.recommendedActionViewed).toBe(
      "onboarding_recommended_action_viewed",
    );
    expect(OnboardingHomeEvents.recommendedActionClicked).toBe(
      "onboarding_recommended_action_clicked",
    );
  });

  it("exposes daily quality analytics events", () => {
    expect(OnboardingHomeEvents.dailyQualityHomeViewed).toBe(
      "daily_quality_home_viewed",
    );
    expect(OnboardingHomeEvents.dailyQualityTopSignalShown).toBe(
      "daily_quality_top_signal_shown",
    );
    expect(OnboardingHomeEvents.dailyQualityActionOpened).toBe(
      "daily_quality_action_opened",
    );
    expect(OnboardingHomeEvents.dailyQualityItemReviewed).toBe(
      "daily_quality_item_reviewed",
    );
    expect(OnboardingHomeEvents.dailyQualityEmptyStateViewed).toBe(
      "daily_quality_empty_state_viewed",
    );
    expect(OnboardingHomeEvents.weeklyQualityReviewOpened).toBe(
      "weekly_quality_review_opened",
    );
  });

  it("normalizes unsafe or empty properties", () => {
    expect(
      normalizeOnboardingEventProperties({
        email: "user@example.com",
        goal: "monitor_production_ai_app",
        empty: null,
        api_key: "secret",
      }),
    ).toEqual({
      goal: "monitor_production_ai_app",
    });
  });
});
