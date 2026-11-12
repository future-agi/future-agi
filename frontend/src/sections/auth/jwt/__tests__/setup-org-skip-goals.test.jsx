/* eslint-disable react/prop-types */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, userEvent, waitFor } from "src/utils/test-utils";
import SetupOrganization from "../setup-org";

const OWNER = { email: "owner@example.com", organization_role: "Owner" };
const MEMBER = { email: "member@example.com", organization_role: "Member" };

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  enqueueSnackbar: vi.fn(),
  redirectAfterOrgSetup: vi.fn(),
  user: null,
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
  useAuthContext: () => ({ user: mocks.user }),
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
const ROLE = "Product Manager / Analyst";
const ROLE_HEADING = "What's your role";
const GOALS_HEADING = "Tell us about your goals";
const TEAM_HEADING = "Collaborate with your team";

// The backend's stored answers, and the onboarding requests a test holds.
let server;
let holdReads;
let holdSaves;
let heldReads;
let heldSaves;

function hold(queue, body) {
  return new Promise((resolve) => {
    queue.push(() => resolve(body));
  });
}

// A read answers with what was stored when it arrived, even if it lands later.
function respond(url, config) {
  if (url === ONBOARDING_URL) {
    const body = {
      data: {
        result: { role: server.role, goals: server.goals, completed: false },
      },
    };
    return holdReads ? hold(heldReads, body) : Promise.resolve(body);
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

function save(_url, payload) {
  server = { role: payload.role, goals: payload.goals };
  const body = {
    status: 200,
    data: {
      status: true,
      result: { message: "Onboarding data saved successfully", data: payload },
    },
  };
  return holdSaves ? hold(heldSaves, body) : Promise.resolve(body);
}

const flush = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 50));
  });

async function release(queue) {
  await act(async () => {
    queue.splice(0).forEach((answer) => answer());
    await new Promise((resolve) => setTimeout(resolve, 50));
  });
}

function openStep(step) {
  window.history.replaceState({}, "", `/auth/jwt/setup-org?step=${step}`);
}

const currentStep = () =>
  new URLSearchParams(window.location.search).get("step");

const newClient = () =>
  new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

async function renderSetup(client = newClient()) {
  let view;
  await act(async () => {
    view = render(
      <QueryClientProvider client={client}>
        <SetupOrganization />
      </QueryClientProvider>,
    );
  });
  return view;
}

const continueButton = () =>
  screen.getByRole("button", { name: "Continue", exact: true });

async function pickRoleAndContinue(user) {
  expect(screen.getByText(ROLE_HEADING)).toBeInTheDocument();
  await user.click(screen.getByRole("radio", { name: ROLE }));
  await user.click(continueButton());
  expect(await screen.findByText(GOALS_HEADING)).toBeInTheDocument();
}

async function skipGoals(user) {
  await user.click(screen.getByText("Skip for now"));
  await waitFor(() =>
    expect(mocks.post).toHaveBeenCalledWith(ONBOARDING_URL, {
      role: ROLE,
      goals: [],
    }),
  );
}

async function expectGoalsStepKeepsRole() {
  expect(await screen.findByText(GOALS_HEADING)).toBeInTheDocument();
  await flush();
  expect(screen.queryByText(ROLE_HEADING)).not.toBeInTheDocument();
  expect(currentStep()).toBe("1");
  expect(continueButton()).toBeEnabled();
}

async function tabTo(user, element) {
  for (let i = 0; i < 30 && document.activeElement !== element; i += 1) {
    await user.tab();
  }
  expect(element).toHaveFocus();
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.get.mockReset();
  mocks.post.mockReset();
  localStorage.clear();
  mocks.user = OWNER;
  server = { role: "", goals: [] };
  holdReads = false;
  holdSaves = false;
  heldReads = [];
  heldSaves = [];
  mocks.get.mockImplementation(respond);
  mocks.post.mockImplementation(save);
});

// WALK-ONBOARDING-GOALS-SKIP-LOOPS: a fast "Skip for now" saved the answers
// and then put the user back on an earlier step with the role cleared.
describe("Skip for now on the goals step (WALK-ONBOARDING-GOALS-SKIP-LOOPS)", () => {
  it("moves an owner forward when the first answers land during the save", async () => {
    holdReads = true;
    holdSaves = true;
    openStep(0);
    const user = userEvent.setup();
    await renderSetup();

    await pickRoleAndContinue(user);
    await skipGoals(user);

    // The read sent when the page opened lands now, from before the save.
    await release(heldReads);
    expect(screen.queryByText(ROLE_HEADING)).not.toBeInTheDocument();
    expect(screen.getByText(GOALS_HEADING)).toBeInTheDocument();
    expect(currentStep()).toBe("1");

    await release(heldSaves);
    expect(await screen.findByText(TEAM_HEADING)).toBeInTheDocument();
    expect(currentStep()).toBe("2");
    expect(mocks.post).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "Back" }));
    await expectGoalsStepKeepsRole();
  });

  it("keeps the saved role when the first answers land after the save", async () => {
    holdReads = true;
    openStep(0);
    const user = userEvent.setup();
    await renderSetup();

    await pickRoleAndContinue(user);
    await skipGoals(user);
    expect(await screen.findByText(TEAM_HEADING)).toBeInTheDocument();

    await release(heldReads);
    expect(screen.getByText(TEAM_HEADING)).toBeInTheDocument();
    expect(currentStep()).toBe("2");

    await user.click(screen.getByRole("button", { name: "Back" }));
    await expectGoalsStepKeepsRole();
  });

  it("takes an invited member who skips out of onboarding, not back a step", async () => {
    mocks.user = MEMBER;
    holdReads = true;
    holdSaves = true;
    openStep(0);
    const user = userEvent.setup();
    await renderSetup();

    await pickRoleAndContinue(user);
    await skipGoals(user);

    await release(heldReads);
    expect(screen.queryByText(ROLE_HEADING)).not.toBeInTheDocument();
    expect(screen.getByText(GOALS_HEADING)).toBeInTheDocument();

    await release(heldSaves);
    await waitFor(() =>
      expect(mocks.redirectAfterOrgSetup).toHaveBeenCalledWith(
        "/dashboard/develop",
      ),
    );
    expect(mocks.redirectAfterOrgSetup).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(ROLE_HEADING)).not.toBeInTheDocument();
  });

  it("shows the saved answers when the goals step opens again after the save", async () => {
    const client = newClient();
    openStep(0);
    const user = userEvent.setup();
    const view = await renderSetup(client);
    await waitFor(() => expect(mocks.get).toHaveBeenCalledWith(ONBOARDING_URL));
    await flush();

    await pickRoleAndContinue(user);
    await skipGoals(user);
    expect(await screen.findByText(TEAM_HEADING)).toBeInTheDocument();
    view.unmount();

    // Only what the app kept from the save can fill the form this time.
    holdReads = true;
    openStep(1);
    await renderSetup(client);
    await expectGoalsStepKeepsRole();
  });
});

// W10-AUTH-ORG-U07: the onboarding controls work without a mouse.
describe("onboarding controls from the keyboard (W10-AUTH-ORG-U07)", () => {
  it("reaches and operates Skip for now with the keyboard", async () => {
    server = { role: ROLE, goals: [] };
    openStep(1);
    const user = userEvent.setup();
    await renderSetup();
    await waitFor(() => expect(continueButton()).toBeEnabled(), {
      timeout: 5000,
    });

    const skip = screen.getByRole("button", { name: "Skip for now" });
    await tabTo(user, skip);
    await user.keyboard("{Enter}");

    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith(ONBOARDING_URL, {
        role: ROLE,
        goals: [],
      }),
    );
    expect(await screen.findByText(TEAM_HEADING)).toBeInTheDocument();
  });

  it("adds and removes a teammate with the keyboard", async () => {
    server = { role: ROLE, goals: [] };
    openStep(2);
    const user = userEvent.setup();
    await renderSetup();
    await waitFor(
      () =>
        expect(screen.getByDisplayValue("owner@example.com")).toBeDisabled(),
      { timeout: 5000 },
    );
    expect(screen.getAllByRole("textbox", { name: "Email" })).toHaveLength(1);

    const add = screen.getByRole("button", { name: "Add" });
    await tabTo(user, add);
    await user.keyboard(" ");
    const remove = await screen.findByRole("button", {
      name: "Remove teammate",
    });
    expect(screen.getAllByRole("textbox", { name: "Email" })).toHaveLength(2);

    await tabTo(user, remove);
    await user.keyboard("{Enter}");
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Remove teammate" }),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getAllByRole("textbox", { name: "Email" })).toHaveLength(1);
  });
});

// Sign-up, after the goals screen: saving the goals shows "Profile updated
// successfully" once. The page asks for it once per save.
describe("goals step confirmation (sign-up)", () => {
  it("asks for 'Profile updated successfully' once per save", async () => {
    openStep(0);
    const user = userEvent.setup();
    await renderSetup();

    await pickRoleAndContinue(user);
    await user.click(screen.getAllByRole("checkbox")[0]);
    await user.click(continueButton());
    expect(await screen.findByText(TEAM_HEADING)).toBeInTheDocument();
    await flush();

    expect(mocks.post).toHaveBeenCalledTimes(1);
    const confirmations = mocks.enqueueSnackbar.mock.calls.filter(
      ([message]) => message === "Profile updated successfully",
    );
    expect(confirmations).toHaveLength(1);
  });
});
