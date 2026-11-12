/* eslint-disable react/prop-types */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, userEvent, waitFor } from "src/utils/test-utils";
import SetupOrganization from "../setup-org";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  enqueueSnackbar: vi.fn(),
  redirectAfterOrgSetup: vi.fn(),
}));

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
    user: { email: "owner@example.com", organization_role: "Owner" },
  }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isOSS: false, isSuccess: true }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("../common", async (importOriginal) => ({
  ...(await importOriginal()),
  redirectAfterOrgSetup: mocks.redirectAfterOrgSetup,
}));
vi.mock("../RightSectionAuth", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
// Native inputs bound to the real form, so the steps behave as in the product.
vi.mock("src/components/RadioField/RadioField", async () => {
  const { Controller } = await import("react-hook-form");
  return {
    default: ({ control, fieldName, options }) => (
      <Controller
        control={control}
        name={fieldName}
        render={({ field, fieldState }) => (
          <div role="radiogroup" aria-label="Role">
            {options.map((option) => (
              <label key={option.value}>
                <input
                  type="radio"
                  name={fieldName}
                  value={option.value}
                  checked={field.value === option.value}
                  onChange={() => field.onChange(option.value)}
                />
                {option.label}
              </label>
            ))}
            {fieldState.error && <span>{fieldState.error.message}</span>}
          </div>
        )}
      />
    ),
  };
});
vi.mock("src/components/FormCheckboxField", async () => {
  const { Controller } = await import("react-hook-form");
  return {
    FormCheckboxField: ({ control, fieldName, label }) => (
      <Controller
        control={control}
        name={fieldName}
        render={({ field }) => (
          <label>
            <input
              type="checkbox"
              checked={!!field.value}
              onChange={(event) => field.onChange(event.target.checked)}
            />
            {label}
          </label>
        )}
      />
    ),
  };
});
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
    FormSearchSelectFieldState: forwardRef(function MockRoleSelect(
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

const ONBOARDING_URL = "/accounts/user-onboarding/";
const SAVED_ROLE = "Product Manager / Analyst";
const ROLE_HEADING = "What's your role";
const GOALS_HEADING = "Tell us about your goals";
const TEAM_HEADING = "Collaborate with your team";
const ROLE_ERROR = "Please select a role or enter your role";

let savedRole = "";
let onboardingPending = false;

function respond(url, config) {
  if (url === ONBOARDING_URL) {
    if (onboardingPending) return new Promise(() => {});
    return Promise.resolve({
      data: { result: { role: savedRole, goals: [], completed: false } },
    });
  }
  return Promise.resolve({
    data: {
      result: {
        org_name: "Test Organization",
        results: config?.params
          ? []
          : [
              {
                id: "owner",
                email: "owner@example.com",
                name: "Owner",
                organization_role: "Owner",
              },
            ],
      },
    },
  });
}

function openStep(step) {
  window.history.replaceState({}, "", `/auth/jwt/setup-org?step=${step}`);
}

const currentStep = () =>
  new URLSearchParams(window.location.search).get("step");

async function renderSetup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  await act(async () => {
    render(
      <QueryClientProvider client={client}>
        <SetupOrganization />
      </QueryClientProvider>,
    );
  });
}

describe("organization setup step navigation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.post.mockReset();
    localStorage.clear();
    savedRole = "";
    onboardingPending = false;
    mocks.get.mockImplementation(respond);
  });

  it("sends a reload on the goals step with no saved role back to the role step", async () => {
    openStep(1);
    await renderSetup();

    expect(
      await screen.findByText(ROLE_HEADING, {}, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(screen.queryByText(GOALS_HEADING)).not.toBeInTheDocument();
    expect(currentStep()).toBe("0");
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("offers Back on the goals step and keeps the chosen role", async () => {
    savedRole = SAVED_ROLE;
    openStep(1);
    const user = userEvent.setup();
    await renderSetup();

    expect(screen.getByText(GOALS_HEADING)).toBeInTheDocument();
    await waitFor(
      () =>
        expect(
          screen.getByRole("button", { name: "Continue", exact: true }),
        ).toBeEnabled(),
      { timeout: 5000 },
    );
    await user.click(screen.getByRole("button", { name: "Back" }));

    expect(await screen.findByText(ROLE_HEADING)).toBeInTheDocument();
    expect(currentStep()).toBe("0");
    expect(screen.getByRole("radio", { name: SAVED_ROLE })).toBeChecked();
  });

  it("offers Back on the team step", async () => {
    savedRole = SAVED_ROLE;
    openStep(2);
    const user = userEvent.setup();
    await renderSetup();

    expect(screen.getByText(TEAM_HEADING)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Back" }));

    expect(await screen.findByText(GOALS_HEADING)).toBeInTheDocument();
    expect(currentStep()).toBe("1");
    await waitFor(
      () =>
        expect(
          screen.getByRole("button", { name: "Continue", exact: true }),
        ).toBeEnabled(),
      { timeout: 5000 },
    );
    expect(screen.getByText(GOALS_HEADING)).toBeInTheDocument();
  });

  it("moves from the goals step to the role step with the browser Back button", async () => {
    savedRole = SAVED_ROLE;
    openStep(0);
    const user = userEvent.setup();
    await renderSetup();

    await waitFor(
      () =>
        expect(screen.getByRole("radio", { name: SAVED_ROLE })).toBeChecked(),
      { timeout: 5000 },
    );
    await user.click(
      screen.getByRole("button", { name: "Continue", exact: true }),
    );
    expect(await screen.findByText(GOALS_HEADING)).toBeInTheDocument();
    expect(currentStep()).toBe("1");

    await act(async () => {
      window.history.back();
    });

    expect(
      await screen.findByText(ROLE_HEADING, {}, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(currentStep()).toBe("0");
    expect(screen.getByRole("radio", { name: SAVED_ROLE })).toBeChecked();
  });

  it("takes Skip for now without a role to the role step and says why", async () => {
    onboardingPending = true;
    openStep(1);
    const user = userEvent.setup();
    await renderSetup();

    expect(screen.getByText(GOALS_HEADING)).toBeInTheDocument();
    await user.click(screen.getByText("Skip for now"));

    expect(await screen.findByText(ROLE_HEADING)).toBeInTheDocument();
    expect(currentStep()).toBe("0");
    expect(screen.getByText(ROLE_ERROR)).toBeInTheDocument();
    expect(mocks.post).not.toHaveBeenCalled();
  });
});
