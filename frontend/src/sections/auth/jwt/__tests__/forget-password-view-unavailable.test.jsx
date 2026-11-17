/* eslint-disable react/prop-types */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import ForgotPasswordView from "../forget-password-view";

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  navigate: vi.fn(),
  enqueueSnackbar: vi.fn(),
}));

vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => mocks.navigate,
}));
vi.mock("src/utils/axios", () => ({
  default: { post: mocks.post },
  endpoints: {
    auth: { passwordResetInitiate: "/accounts/password-reset-initiate/" },
  },
}));
vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: mocks.enqueueSnackbar }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../RightSectionAuth", () => ({ default: () => null }));
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

const email = "someone@example.com";
const unavailable =
  "This site can't send email, so we couldn't send a reset link. Ask your administrator to reset your password.";
const mailed = `If an account matches ${email}, you will receive an email with instructions on how to reset your password shortly.`;

function respondWith(result) {
  mocks.post.mockResolvedValue({
    status: 200,
    data: { status: true, result },
  });
}

async function requestReset() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={client}>
      <ForgotPasswordView />
    </QueryClientProvider>,
  );
  await user.type(screen.getByRole("textbox", { name: "Email" }), email);
  await user.click(screen.getByRole("button", { name: "Send Request" }));
  await waitFor(() =>
    expect(mocks.post).toHaveBeenCalledWith(
      "/accounts/password-reset-initiate/",
      { email },
    ),
  );
}

describe("forgot password when the site cannot send email", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.post.mockReset();
  });

  it("shows the no-email answer as a warning, not a success", async () => {
    respondWith({ message: unavailable, email_unavailable: true });
    await requestReset();

    await waitFor(() =>
      expect(mocks.enqueueSnackbar).toHaveBeenCalledWith(unavailable, {
        variant: "warning",
      }),
    );
    expect(mocks.enqueueSnackbar).not.toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ variant: "success" }),
    );
    expect(mocks.navigate).not.toHaveBeenCalled();
  });

  it("still shows a mailed link as a success", async () => {
    respondWith({ message: mailed });
    await requestReset();

    await waitFor(() =>
      expect(mocks.enqueueSnackbar).toHaveBeenCalledWith(mailed, {
        variant: "success",
      }),
    );
  });

  it("still opens a returned link instead of showing a message", async () => {
    respondWith({
      message: "Use the link below to reset your password.",
      reset_link: "http://localhost:3031/auth/jwt/verify/uid/token",
    });
    await requestReset();

    await waitFor(() =>
      expect(mocks.navigate).toHaveBeenCalledWith("/auth/jwt/verify/uid/token"),
    );
    expect(mocks.enqueueSnackbar).not.toHaveBeenCalled();
  });
});
