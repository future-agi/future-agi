import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  render,
  screen,
  userEvent,
  waitFor,
} from "src/utils/test-utils";
import { endpoints } from "src/utils/axios";
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
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: mocks.get, post: mocks.post },
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
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

const listedRole = "Backend / Platform Engineer / DevOps";
const typedRole = "SYNTHETIC_ROLE_TEXT_3c9d";
const goal = "Monitor LLMs and Agents";

const signUpEvents = () =>
  mocks.mixpanel.track.mock.calls.filter(
    ([eventName]) => eventName === Events.signUpCompleted,
  );

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  localStorage.setItem("signupProvider", "google");
  window.history.replaceState({}, "", "/auth/jwt/setup-org");
  mocks.get.mockImplementation(async (url) => {
    if (url === endpoints.auth.user_onboarding_info) {
      return { data: { result: { role: "", goals: [], completed: false } } };
    }
    return { data: { result: { org_name: "", results: [] } } };
  });
  mocks.post.mockImplementation(async (_url, payload) => ({
    data: { result: { message: "saved", data: payload } },
  }));
});

afterEach(() => cleanup());

describe("onboarding profile event fields", () => {
  it.each([
    ["typed", typedRole, { role_is_custom: true }],
    ["listed", listedRole, { role: listedRole, role_is_custom: false }],
  ])(
    "sends a %s role as the listed role or a custom-role flag",
    async (kind, role, roleFields) => {
      const user = userEvent.setup();
      const client = new QueryClient({
        defaultOptions: {
          queries: { retry: false },
          mutations: { retry: false },
        },
      });
      render(
        <QueryClientProvider client={client}>
          <SetupOrganization />
        </QueryClientProvider>,
      );
      const input = await screen.findByPlaceholderText(
        "Tell us about your role",
      );
      if (kind === "typed") await user.type(input, role);
      else await user.click(screen.getByText(role));
      await user.click(
        screen.getByRole("button", { name: "Continue", exact: true }),
      );
      await user.click(screen.getByText(goal));
      await user.click(
        screen.getByRole("button", { name: "Continue", exact: true }),
      );

      await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
      expect(mocks.post).toHaveBeenCalledWith(
        endpoints.auth.user_onboarding_info,
        { role, goals: [goal] },
      );
      await waitFor(() => expect(signUpEvents()).toHaveLength(1));
      expect(signUpEvents()[0][1]).toEqual({
        email: "owner@example.test",
        goals: [goal],
        method: "google",
        ...roleFields,
      });
      if (kind === "typed") {
        expect(JSON.stringify(mocks.mixpanel.track.mock.calls)).not.toContain(
          typedRole,
        );
      }
    },
  );
});
