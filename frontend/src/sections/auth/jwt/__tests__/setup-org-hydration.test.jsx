import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import {
  act,
  cleanup,
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import { endpoints } from "src/utils/axios";
import { handleError } from "src/utils/queryErrorHandler";
import SetupOrganization from "../setup-org";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  onboarding: vi.fn(),
  enqueueSnackbar: vi.fn(),
  globalToast: vi.fn(),
}));

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: mocks.get, post: mocks.post },
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: mocks.globalToast,
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
  useDeploymentMode: () => ({ isOSS: true, isSuccess: true }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("../RightSectionAuth", () => ({ default: () => null }));

const presetRole = "Backend / Platform Engineer / DevOps";
const customRole = "Reliability engineer";
const goal = "Monitor LLMs and Agents";
const otherGoal = "Run Evaluations";
const queryKey = ["user-role-goals"];
const clients = [];

function response(role = "", goals = []) {
  return {
    data: { result: { role, goals, completed: !!role && !!goals.length } },
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function createClient() {
  const client = new QueryClient({
    // The app's global handler, so tests see the toasts users would.
    queryCache: new QueryCache({ onError: handleError }),
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  return client;
}

function renderSetup(client = createClient(), getStarted = false) {
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <SetupOrganization getStarted={getStarted} />
      </QueryClientProvider>,
    ),
  };
}

function checkbox(label) {
  // The shared custom goal card labels its checkbox "controlled", not the goal.
  return within(
    screen.getByText(label).closest(".MuiFormControl-root"),
  ).getByRole("checkbox");
}

async function continueStep(user) {
  const button = screen.getByRole("button", { name: "Continue", exact: true });
  expect(button).toBeEnabled();
  await user.click(button);
}

async function visitStep(step) {
  await act(async () => {
    window.history.pushState({}, "", `/auth/jwt/setup-org?step=${step}`);
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
}

async function expectSaved(role, goals) {
  await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
  expect(mocks.post).toHaveBeenCalledWith(endpoints.auth.user_onboarding_info, {
    role,
    goals,
  });
  await screen.findByText("Collaborate with your team");
}

beforeEach(() => {
  vi.resetAllMocks();
  localStorage.clear();
  window.history.replaceState({}, "", "/auth/jwt/setup-org");
  mocks.onboarding.mockResolvedValue(response());
  mocks.get.mockImplementation((url) => {
    if (url === endpoints.auth.user_onboarding_info) return mocks.onboarding();
    if (url === endpoints.auth.create_org) {
      return Promise.resolve({
        data: { result: { org_name: "Test", results: [] } },
      });
    }
    throw new Error(`Unexpected GET ${url}`);
  });
  mocks.post.mockImplementation(async (url, payload) => ({
    data: {
      result: { message: "Onboarding data saved successfully", data: payload },
    },
  }));
});

afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});

describe("onboarding profile initialization", () => {
  it.each(["custom", "preset"])(
    "waits for the initial read, then saves a %s role and one goal click",
    async (roleKind) => {
      const initial = deferred();
      mocks.onboarding.mockReturnValueOnce(initial.promise);
      const user = userEvent.setup();
      renderSetup();

      expect(
        screen.getByRole("progressbar", { name: "Loading your profile" }),
      ).toBeInTheDocument();
      expect(
        screen.queryByPlaceholderText("Tell us about your role"),
      ).not.toBeInTheDocument();
      expect(screen.queryByRole("radio")).not.toBeInTheDocument();
      expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Continue" }),
      ).not.toBeInTheDocument();
      expect(screen.queryByText("Skip for now")).not.toBeInTheDocument();
      expect(mocks.post).not.toHaveBeenCalled();

      await act(async () => initial.resolve(response()));
      const input = await screen.findByPlaceholderText(
        "Tell us about your role",
      );
      const role = roleKind === "custom" ? customRole : presetRole;
      if (roleKind === "custom") await user.type(input, role);
      else await user.click(screen.getByText(role));
      await continueStep(user);
      screen
        .getAllByRole("checkbox")
        .forEach((field) => expect(field).not.toBeChecked());
      await user.click(screen.getByText(goal));
      expect(checkbox(goal)).toBeChecked();
      await continueStep(user);
      await expectSaved(role, [goal]);
    },
  );

  it.each(["success", "failure"])(
    "keeps the edited role and goals through a background refetch %s and recovery",
    async (outcome) => {
      const client = createClient();
      // A warm cache can initialize the form, but the mount refetch cannot own the draft.
      client.setQueryData(queryKey, response(presetRole, [otherGoal]));
      const refetch = deferred();
      mocks.onboarding.mockReturnValueOnce(refetch.promise);
      const user = userEvent.setup();
      renderSetup(client);
      const input = await screen.findByPlaceholderText(
        "Tell us about your role",
      );
      await user.type(input, customRole);
      await continueStep(user);
      expect(checkbox(otherGoal)).toBeChecked();
      await user.click(screen.getByText(otherGoal));
      await user.click(screen.getByText(goal));
      const selected = checkbox(goal);

      await act(async () => {
        if (outcome === "success")
          refetch.resolve(response("Server role", [otherGoal]));
        else refetch.reject(new Error("Background read failed"));
      });
      await waitFor(() =>
        expect(client.getQueryState(queryKey).fetchStatus).toBe("idle"),
      );
      expect(checkbox(goal)).toBe(selected);
      expect(selected).toBeChecked();
      expect(checkbox(otherGoal)).not.toBeChecked();
      expect(
        screen.queryByText("Couldn't load your profile"),
      ).not.toBeInTheDocument();

      mocks.onboarding.mockResolvedValue(
        response("Another server role", [otherGoal]),
      );
      await act(async () => client.refetchQueries({ queryKey }));
      expect(checkbox(goal)).toBe(selected);
      expect(selected).toBeChecked();
      await visitStep(0);
      expect(
        screen.getByPlaceholderText("Tell us about your role"),
      ).toHaveValue(customRole);
      await continueStep(user);
      expect(checkbox(goal)).toBeChecked();
      expect(checkbox(otherGoal)).not.toBeChecked();
      await continueStep(user);
      await expectSaved(customRole, [goal]);
    },
  );

  it("shows an initial-read error and retries before enabling the form", async () => {
    // An API error envelope: the app's global handler would toast its result.
    mocks.onboarding.mockRejectedValueOnce(
      Object.assign(new Error("Initial read failed"), {
        result: "Profile unavailable",
        statusCode: 500,
      }),
    );
    const retry = deferred();
    mocks.onboarding.mockReturnValueOnce(retry.promise);
    const user = userEvent.setup();
    renderSetup();
    await screen.findByText("Couldn't load your profile");
    expect(mocks.globalToast).not.toHaveBeenCalled();
    expect(
      screen.queryByPlaceholderText("Tell us about your role"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Continue" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(
      screen.getByRole("progressbar", { name: "Loading your profile" }),
    ).toBeInTheDocument();
    await act(async () => retry.resolve(response(customRole, [goal])));
    expect(
      await screen.findByPlaceholderText("Tell us about your role"),
    ).toHaveValue(customRole);
    await continueStep(user);
    expect(checkbox(goal)).toBeChecked();
    expect(mocks.onboarding).toHaveBeenCalledTimes(2);
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("can replace a saved custom role with a preset and skip selected goals", async () => {
    mocks.onboarding.mockResolvedValue(response(customRole, [goal]));
    const user = userEvent.setup();
    renderSetup();
    expect(
      await screen.findByPlaceholderText("Tell us about your role"),
    ).toHaveValue(customRole);
    await user.click(screen.getByText(presetRole));
    expect(screen.getByPlaceholderText("Tell us about your role")).toHaveValue(
      "",
    );
    await continueStep(user);
    expect(checkbox(goal)).toBeChecked();
    await user.click(screen.getByText("Skip for now"));
    await expectSaved(presetRole, []);
  });

  it("seeds remounts from the saved profile, not an older in-flight read", async () => {
    const user = userEvent.setup();
    const { client, unmount } = renderSetup();
    await user.type(
      await screen.findByPlaceholderText("Tell us about your role"),
      customRole,
    );
    await continueStep(user);
    await user.click(screen.getByText(goal));
    const staleRead = deferred();
    mocks.onboarding.mockReturnValueOnce(staleRead.promise);
    let refetch;
    await act(async () => {
      refetch = client.refetchQueries({ queryKey });
    });
    await continueStep(user);
    await expectSaved(customRole, [goal]);
    await act(async () => {
      staleRead.resolve(response("Stale server role", []));
      await refetch;
    });
    expect(client.getQueryData(queryKey)).toEqual(response(customRole, [goal]));

    await visitStep(0);
    expect(screen.getByPlaceholderText("Tell us about your role")).toHaveValue(
      customRole,
    );
    await continueStep(user);
    expect(checkbox(goal)).toBeChecked();
    unmount();
    await visitStep(0);
    const remountRead = deferred();
    mocks.onboarding.mockReturnValueOnce(remountRead.promise);
    renderSetup(client);
    expect(
      await screen.findByPlaceholderText("Tell us about your role"),
    ).toHaveValue(customRole);
    await continueStep(user);
    expect(checkbox(goal)).toBeChecked();
    await act(async () => remountRead.resolve(response(customRole, [goal])));
    expect(checkbox(goal)).toBeChecked();
  });

  it("does not block organization-only setup on a profile read", async () => {
    mocks.onboarding.mockReturnValue(new Promise(() => {}));
    renderSetup(createClient(), true);
    expect(
      await screen.findByRole("button", { name: "Continue" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("progressbar", { name: "Loading your profile" }),
    ).not.toBeInTheDocument();
    expect(mocks.onboarding).not.toHaveBeenCalled();
  });
});
