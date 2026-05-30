import { describe, expect, it, vi } from "vitest";
import { trackPostHogEvent } from "src/utils/PostHog";
import {
  buildSetupOrgInvitesSavedProperties,
  buildSetupOrgProfileSavedProperties,
  buildSetupOrgQuickStartClickedProperties,
  buildSetupOrgQuickStartsViewedProperties,
  SetupOrgEvents,
  trackSetupOrgInvitesSaved,
  trackSetupOrgProfileSaved,
  trackSetupOrgQuickStartClicked,
  trackSetupOrgQuickStartsViewed,
} from "./setup-org-analytics";

vi.mock("src/utils/PostHog", () => ({
  trackPostHogEvent: vi.fn(),
}));

describe("setup-org analytics", () => {
  it("builds profile analytics without email or full goal payload", () => {
    expect(
      buildSetupOrgProfileSavedProperties({
        goals: [
          "Monitor a production AI app",
          "Evaluate quality on data or traces",
        ],
        provider: "google",
        quickStartGoal: "monitor_production_ai_app",
        quickStartId: "observe",
        quickStartPrimaryPath: "observe",
        quickStartRequested: true,
        role: "AI Builder",
      }),
    ).toEqual({
      role: "AI Builder",
      primary_goal: "Monitor a production AI app",
      goal_count: 2,
      method: "google",
      quick_start_goal: "monitor_production_ai_app",
      quick_start_id: "observe",
      quick_start_primary_path: "observe",
      quick_start_requested: true,
    });
  });

  it("builds invite analytics without member emails", () => {
    expect(
      buildSetupOrgInvitesSavedProperties({
        members: [
          {
            email: "new@example.com",
            organization_role: "Admin",
          },
          {
            email: "viewer@example.com",
            organization_role: "Viewer",
          },
          {
            email: "existing@example.com",
            organization_role: "Owner",
            disabled: true,
          },
        ],
      }),
    ).toEqual({
      invited_member_count: 2,
      roles_assigned: ["Admin", "Viewer"],
    });
  });

  it("builds quick-start view analytics without user payloads", () => {
    expect(
      buildSetupOrgQuickStartsViewedProperties({
        quickStarts: [
          {
            id: "observe",
            goal: "monitor_production_ai_app",
            primaryPath: "observe",
            featured: true,
          },
          {
            id: "prompt",
            goal: "improve_prompts",
            primaryPath: "prompt",
            featured: false,
          },
          {
            id: "prompt_retry",
            goal: "improve_prompts",
            primaryPath: "prompt",
            featured: false,
          },
        ],
      }),
    ).toEqual({
      quick_start_count: 3,
      featured_quick_start_count: 1,
      quick_start_ids: ["observe", "prompt", "prompt_retry"],
      quick_start_goals: [
        "monitor_production_ai_app",
        "improve_prompts",
        "improve_prompts",
      ],
      quick_start_primary_paths: ["observe", "prompt"],
    });
  });

  it("builds quick-start click analytics", () => {
    expect(
      buildSetupOrgQuickStartClickedProperties({
        quickStartGoal: "monitor_production_ai_app",
        quickStartId: "observe",
        quickStartPrimaryPath: "observe",
      }),
    ).toEqual({
      quick_start_goal: "monitor_production_ai_app",
      quick_start_id: "observe",
      quick_start_primary_path: "observe",
    });
  });

  it("tracks setup events through PostHog", () => {
    trackSetupOrgQuickStartsViewed({
      quickStarts: [
        {
          id: "observe",
          goal: "monitor_production_ai_app",
          primaryPath: "observe",
          featured: true,
        },
      ],
    });
    trackSetupOrgQuickStartClicked({
      quickStartGoal: "monitor_production_ai_app",
      quickStartId: "observe",
      quickStartPrimaryPath: "observe",
    });
    trackSetupOrgProfileSaved({
      goals: ["Monitor a production AI app"],
      quickStartGoal: "monitor_production_ai_app",
      quickStartId: "observe",
      quickStartPrimaryPath: "observe",
      quickStartRequested: true,
      role: "AI Builder",
    });
    trackSetupOrgInvitesSaved({
      members: [{ email: "new@example.com", organization_role: "Admin" }],
    });

    expect(trackPostHogEvent).toHaveBeenNthCalledWith(
      1,
      SetupOrgEvents.quickStartsViewed,
      {
        quick_start_count: 1,
        featured_quick_start_count: 1,
        quick_start_ids: ["observe"],
        quick_start_goals: ["monitor_production_ai_app"],
        quick_start_primary_paths: ["observe"],
      },
    );
    expect(trackPostHogEvent).toHaveBeenNthCalledWith(
      2,
      SetupOrgEvents.quickStartClicked,
      {
        quick_start_goal: "monitor_production_ai_app",
        quick_start_id: "observe",
        quick_start_primary_path: "observe",
      },
    );
    expect(trackPostHogEvent).toHaveBeenNthCalledWith(
      3,
      SetupOrgEvents.profileSaved,
      {
        role: "AI Builder",
        primary_goal: "Monitor a production AI app",
        goal_count: 1,
        quick_start_goal: "monitor_production_ai_app",
        quick_start_id: "observe",
        quick_start_primary_path: "observe",
        quick_start_requested: true,
      },
    );
    expect(trackPostHogEvent).toHaveBeenNthCalledWith(
      4,
      SetupOrgEvents.invitesSaved,
      {
        invited_member_count: 1,
        roles_assigned: ["Admin"],
      },
    );
  });
});
