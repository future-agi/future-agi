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
vi.mock("src/components/RadioField/RadioField", () => ({
  default: () => null,
}));
vi.mock("src/components/FormCheckboxField", () => ({
  FormCheckboxField: () => null,
}));
// Keep form validation and mutation behavior real, without unrelated field widgets.
vi.mock("src/components/FormTextField/FormTextFieldV2", async () => {
  const { Controller } = await import("react-hook-form");
  return {
    default: ({ control, fieldName, label }) => (
      <Controller
        control={control}
        name={fieldName}
        render={({ field, fieldState }) => (
          <>
            <label>
              {label}
              <input {...field} value={field.value ?? ""} />
            </label>
            {fieldState.error && <span>{fieldState.error.message}</span>}
          </>
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

const email = "newhire@example.com";
const warning =
  "Member added, but the invitation email could not be sent. Open Members to resend the invite or copy an invite link if available.";
const createdMember = { id: "member-1", email, name: "Newhire" };

async function renderSetup(getStarted = true) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  // Flush the resolved GET -> initialData -> form.reset effect chain before
  // interacting. A synchronous render leaves the blank placeholder on screen.
  await act(async () => {
    render(
      <QueryClientProvider client={client}>
        <SetupOrganization getStarted={getStarted} />
      </QueryClientProvider>,
    );
  });
  await waitFor(
    () => {
      expect(mocks.get).toHaveBeenCalledWith("/accounts/team/users/");
      expect(screen.getByDisplayValue("owner@example.com")).toBeDisabled();
    },
    { timeout: 5000 },
  );
}

async function addMember(user, address = email, getStarted = true) {
  await user.click(
    screen.getByText(getStarted ? "Add members" : "Add", { exact: true }),
  );
  const input = screen
    .getAllByRole("textbox", { name: "Email" })
    .find((field) => !field.disabled && !field.value);
  await user.type(input, address);
}

async function submit(user) {
  const button = screen.getByRole("button", { name: "Continue", exact: true });
  await waitFor(() => expect(button).toBeEnabled());
  await user.click(button);
}

function respondWith(members) {
  mocks.post.mockResolvedValue({
    status: 201,
    data: { status: true, result: { created_members: members } },
  });
}

describe("organization setup invitation delivery", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.post.mockReset();
    localStorage.clear();
    window.history.replaceState({}, "", "/auth/jwt/setup-org?step=2");
    mocks.get.mockImplementation(async (url, config) => ({
      data: {
        result:
          url === "/accounts/team/users/"
            ? {
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
              }
            : {},
      },
    }));
  });

  it("reports a healthy invite as sent", async () => {
    respondWith([createdMember]);
    const user = userEvent.setup();
    await renderSetup();
    await addMember(user);
    await submit(user);

    await waitFor(() =>
      expect(mocks.enqueueSnackbar).toHaveBeenCalledWith(
        "Invite sent successfully.",
        { variant: "success" },
      ),
    );
    expect(mocks.post).toHaveBeenCalledWith("/accounts/team/users/", {
      org_name: "Test Organization",
      members: [
        { index: 1, email, name: "Newhire", organization_role: "Member" },
      ],
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows recovery guidance, not sent-success, and acknowledges without inviting twice", async () => {
    respondWith([{ ...createdMember, invite_email_warning: warning }]);
    const user = userEvent.setup();
    await renderSetup();
    await addMember(user);
    await submit(user);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(`${email}: ${warning}`);
    expect(
      screen.getByText("Members added", { exact: true }),
    ).toBeInTheDocument();
    expect(mocks.enqueueSnackbar).not.toHaveBeenCalledWith(
      "Invite sent successfully.",
      expect.anything(),
    );
    await user.click(screen.getByRole("button", { name: "Done", exact: true }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(mocks.post).toHaveBeenCalledTimes(1);
  });

  it.each([null, "/dashboard/settings/members"])(
    "navigates only after warning acknowledgment (returnTo=%s)",
    async (returnTo) => {
      if (returnTo) localStorage.setItem("redirectUrl", returnTo);
      respondWith([{ ...createdMember, invite_email_warning: warning }]);
      const user = userEvent.setup();
      await renderSetup(false);
      await addMember(user, email, false);
      await submit(user);

      expect(await screen.findByRole("alert")).toHaveTextContent(warning);
      expect(mocks.redirectAfterOrgSetup).not.toHaveBeenCalled();
      expect(
        screen.queryByRole("button", { name: "Continue", exact: true }),
      ).not.toBeInTheDocument();
      await user.click(
        screen.getByRole("button", { name: "Continue to dashboard" }),
      );
      expect(mocks.redirectAfterOrgSetup).toHaveBeenCalledTimes(1);
      expect(mocks.redirectAfterOrgSetup).toHaveBeenCalledWith(
        returnTo || "/dashboard/get-started",
      );
      expect(localStorage.getItem("redirectUrl")).toBeNull();
      if (returnTo) expect(localStorage.getItem("initial-render")).toBe("done");
      expect(mocks.post).toHaveBeenCalledTimes(1);
    },
  );

  it("identifies only the failed delivery in a mixed bulk result", async () => {
    respondWith([
      createdMember,
      {
        id: "member-2",
        email: "failed@example.com",
        invite_email_warning: warning,
      },
    ]);
    const user = userEvent.setup();
    await renderSetup();
    await addMember(user);
    await addMember(user, "failed@example.com");
    await submit(user);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(`failed@example.com: ${warning}`);
    expect(alert).not.toHaveTextContent(email);
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(mocks.enqueueSnackbar).not.toHaveBeenCalledWith(
      "Invite sent successfully.",
      expect.anything(),
    );
  });

  it.each([
    "User is already a member of this organization",
    "User already exists in the other organization.",
  ])(
    "retains delivery warnings and retries only corrected rows: %s",
    async (memberError) => {
      const failedEmail = "duplicate@example.com";
      const correctedEmail = "corrected@example.com";
      const message =
        "Some members could not be added. Correct the errors and try again.";
      // Axios rejects with the normalized response body plus statusCode, not an
      // Axios response wrapper. GeneralMethods must preserve result as an object.
      mocks.post
        .mockRejectedValueOnce({
          status: false,
          statusCode: 400,
          type: "validation_error",
          code: "invalid",
          detail: message,
          message,
          error: message,
          result: {
            message,
            workspace: {
              id: "workspace-1",
              name: "default",
              display_name: "Default Workspace",
            },
            created_members: [
              { ...createdMember, invite_email_warning: warning },
            ],
            errors: [{ index: 1, email: failedEmail, error: memberError }],
          },
        })
        .mockResolvedValueOnce({
          status: 201,
          data: {
            status: true,
            result: {
              created_members: [{ id: "member-2", email: correctedEmail }],
            },
          },
        });
      const user = userEvent.setup();
      await renderSetup(false);
      await addMember(user, email, false);
      await addMember(user, failedEmail, false);
      await submit(user);

      expect(await screen.findByRole("alert")).toHaveTextContent(
        `${email}: ${warning}`,
      );
      expect(await screen.findByText(memberError)).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Continue", exact: true }),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Continue to dashboard" }),
      ).not.toBeInTheDocument();
      expect(mocks.redirectAfterOrgSetup).not.toHaveBeenCalled();
      expect(screen.queryByDisplayValue(email)).not.toBeInTheDocument();
      const retryInput = screen.getByDisplayValue(failedEmail);
      expect(retryInput).toBeEnabled();
      expect(
        screen
          .getAllByRole("textbox", { name: "Email" })
          .filter((field) => !field.disabled),
      ).toEqual([retryInput]);
      await user.clear(retryInput);
      await user.type(retryInput, correctedEmail);
      await submit(user);

      await screen.findByRole("button", { name: "Continue to dashboard" });
      expect(mocks.post).toHaveBeenCalledTimes(2);
      expect(
        mocks.post.mock.calls[0][1].members.map((member) => member.email),
      ).toEqual([email, failedEmail]);
      expect(
        mocks.post.mock.calls[1][1].members.map((member) => member.email),
      ).toEqual([correctedEmail]);
      // The earlier mail failure is still actionable after the retry succeeds.
      expect(screen.getByRole("alert")).toHaveTextContent(
        `${email}: ${warning}`,
      );
      expect(screen.getAllByRole("alert")).toHaveLength(1);
      expect(mocks.redirectAfterOrgSetup).not.toHaveBeenCalled();
      await user.click(
        screen.getByRole("button", { name: "Continue to dashboard" }),
      );
      expect(mocks.redirectAfterOrgSetup).toHaveBeenCalledTimes(1);
      expect(mocks.redirectAfterOrgSetup).toHaveBeenCalledWith(
        "/dashboard/get-started",
      );
      expect(mocks.post).toHaveBeenCalledTimes(2);
      expect(mocks.enqueueSnackbar).not.toHaveBeenCalledWith(
        "Invite sent successfully.",
        expect.anything(),
      );
    },
  );

  it("keeps the organization-only success message when no member was invited", async () => {
    respondWith([]);
    const user = userEvent.setup();
    await renderSetup();
    await submit(user);

    await waitFor(() =>
      expect(mocks.enqueueSnackbar).toHaveBeenCalledWith(
        "Organization Setup Successfully.",
        { variant: "success" },
      ),
    );
    expect(mocks.post.mock.calls[0][1].members).toEqual([]);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("still reports member-creation failures as errors", async () => {
    mocks.post.mockRejectedValue({ result: "Error in managing users" });
    const user = userEvent.setup();
    await renderSetup();
    await addMember(user);
    await submit(user);

    await waitFor(() =>
      expect(mocks.enqueueSnackbar).toHaveBeenCalledWith(
        "Error in managing users",
        { variant: "error" },
      ),
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(mocks.enqueueSnackbar).not.toHaveBeenCalledWith(
      "Invite sent successfully.",
      expect.anything(),
    );
  });
});
