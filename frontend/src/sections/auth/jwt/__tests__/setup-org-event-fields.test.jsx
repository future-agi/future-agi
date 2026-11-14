/* eslint-disable react/prop-types */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, userEvent, waitFor } from "src/utils/test-utils";
import SetupOrganization from "../setup-org";
import { Events } from "src/utils/Mixpanel";

const mocks = vi.hoisted(() => {
  vi.stubEnv("VITE_MIXPANEL_TOKEN", "synthetic-project-token");
  return {
    get: vi.fn(),
    post: vi.fn(),
    enqueueSnackbar: vi.fn(),
    mixpanel: {
      init: vi.fn(),
      track: vi.fn(),
      register: vi.fn(),
      get_config: vi.fn(),
      set_config: vi.fn(),
      stop_session_recording: vi.fn(),
    },
  };
});

vi.mock("mixpanel-browser", () => ({ default: mocks.mixpanel }));
vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, post: mocks.post },
  endpoints: {
    auth: {
      create_org: "/accounts/team/users/",
      user_onboarding_info: "/accounts/user-onboarding/",
    },
    settings: {
      teams: {
        getMemberList: "/accounts/team/users/",
        inviteMember: "/accounts/team/users/",
      },
    },
  },
}));
vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: mocks.enqueueSnackbar }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: { email: "owner@example.test", organization_role: "Owner" },
  }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isOSS: false, isSuccess: true }),
}));
vi.mock("../RightSectionAuth", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/FormTextField/FormTextFieldV2", async () => {
  const { Controller } = await import("react-hook-form");
  return {
    default: ({ control, fieldName, label }) => (
      <Controller
        control={control}
        name={fieldName}
        render={({ field }) => (
          <label>
            {label}
            <input {...field} value={field.value ?? ""} />
          </label>
        )}
      />
    ),
  };
});
vi.mock("src/components/FromSearchSelectField", async () => {
  const { forwardRef } = await import("react");
  return {
    FormSearchSelectFieldState: forwardRef(function RoleSelect(
      { label, value, onChange, onBlur, name, options, disabled },
      ref,
    ) {
      return (
        <label>
          {label}
          <select
            ref={ref}
            name={name}
            value={value}
            disabled={disabled}
            onBlur={onBlur}
            onChange={(event) => onChange(event.target.value)}
          >
            {options.map((option, index) => (
              <option key={`${option.value}-${index}`} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      );
    }),
  };
});

const firstEmail = "synthetic_member_9f3a@example.test";
const secondEmail = "synthetic_admin_4b2c@example.test";

describe("organization setup event fields", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    window.history.replaceState({}, "", "/auth/jwt/setup-org?step=2");
    mocks.get.mockImplementation(async (_url, config) => ({
      data: {
        result: {
          org_name: "Test Organization",
          results: config?.params
            ? []
            : [
                {
                  id: "owner",
                  email: "owner@example.test",
                  name: "Owner",
                  organization_role: "Owner",
                },
              ],
        },
      },
    }));
    mocks.post.mockResolvedValue({
      status: 201,
      data: { result: { created_members: [] } },
    });
  });

  it("sends the number of added members and their assigned roles, not the organization name", async () => {
    const user = userEvent.setup();
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    await act(async () => {
      render(
        <QueryClientProvider client={client}>
          <SetupOrganization getStarted />
        </QueryClientProvider>,
      );
    });
    await waitFor(() =>
      expect(screen.getByDisplayValue("owner@example.test")).toBeDisabled(),
    );

    for (const [email, role] of [
      [firstEmail, "Member"],
      [secondEmail, "Admin"],
    ]) {
      await user.click(screen.getByText("Add members"));
      const input = screen
        .getAllByRole("textbox", { name: "Email" })
        .find((field) => !field.disabled && !field.value);
      await user.type(input, email);
      if (role === "Admin") {
        const selects = screen
          .getAllByRole("combobox", { name: "Role" })
          .filter((field) => !field.disabled);
        await user.selectOptions(selects.at(-1), role);
      }
    }
    const continueButton = screen.getByRole("button", { name: "Continue" });
    await waitFor(() => expect(continueButton).toBeEnabled());
    await user.click(continueButton);
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    expect(mocks.post).toHaveBeenCalledWith("/accounts/team/users/", {
      org_name: "Test Organization",
      members: [
        {
          index: 1,
          email: firstEmail,
          name: "Synthetic Member 9f3a",
          organization_role: "Member",
        },
        {
          index: 2,
          email: secondEmail,
          name: "Synthetic Admin 4b2c",
          organization_role: "Admin",
        },
      ],
    });
    await waitFor(() =>
      expect(
        mocks.mixpanel.track.mock.calls.filter(
          ([eventName]) => eventName === Events.setupOrganizationClicked,
        ),
      ).toHaveLength(1),
    );
    const event = mocks.mixpanel.track.mock.calls.find(
      ([eventName]) => eventName === Events.setupOrganizationClicked,
    );
    expect(event[1]).toEqual({
      click: true,
      members_added: 2,
      roles_assigned: ["Member", "Admin"],
    });
    const pageViews = mocks.mixpanel.track.mock.calls.filter(
      ([eventName, properties]) =>
        eventName === Events.pageView && properties?.click?.create_org_clicked,
    );
    expect(pageViews).toHaveLength(1);
    expect(pageViews[0][1].click).toEqual({
      members_added: 2,
      roles_assigned: ["Member", "Admin"],
      create_org_clicked: true,
    });
    const allEvents = JSON.stringify(mocks.mixpanel.track.mock.calls);
    expect(allEvents).not.toContain("Test Organization");
    expect(allEvents).not.toContain(firstEmail);
    expect(allEvents).not.toContain(secondEmail);
    expect(allEvents).not.toContain("Synthetic Member 9f3a");
    expect(allEvents).not.toContain("Synthetic Admin 4b2c");
  });
});
