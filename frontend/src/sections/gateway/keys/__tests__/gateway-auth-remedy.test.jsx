import React from "react";
import { AxiosError } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SnackbarProvider } from "notistack";
import * as auth from "src/auth/context/jwt/utils";
import { resetUser } from "src/utils/Mixpanel";
import client from "src/utils/axios";
import CreateKeyDialog from "../CreateKeyDialog";
import EditKeyDialog from "../EditKeyDialog";
import KeyManagementSection from "../KeyManagementSection";
import GatewaySettingsSection from "../../settings/GatewaySettingsSection";
import { renderPage } from "../../guardrails/__tests__/helpers";

vi.mock("src/config-global", () => ({ HOST_API: "https://offline.invalid" }));
vi.mock("src/utils/Mixpanel", () => ({ resetUser: vi.fn() }));
vi.mock("src/utils/logger", () => ({
  default: { debug: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));
vi.mock("src/utils/recaptchaService", () => ({
  getRecaptchaToken: vi.fn(async () => "fake-recaptcha"),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    orgLevel: 15,
    currentOrganizationId: "org-a",
    isReady: true,
  }),
}));
vi.mock("src/hooks/use-responsive", () => ({ useResponsive: () => true }));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: "signed-in-user" } }),
}));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "fixture-gateway", isLoading: false }),
}));
vi.mock(
  "ag-grid-react",
  async () => await import("../../providers/__tests__/grid-mock"),
);
vi.mock("../../providers/ui/CreditBalanceChip", () => ({
  default: () => null,
}));
vi.mock("../KeyDetailDrawer", () => ({ default: () => null }));
vi.mock("../AllowedProvidersField", () => ({ default: () => null }));
vi.mock("../useKeyAccessOptions", () => ({
  useKeyAccessOptions: () => ({ modelOptions: [] }),
}));

// The settings shell and its request hooks stay real; unrelated tab bodies
// do not participate in these action-error tests.
vi.mock("../../settings/GeneralTab", () => ({ default: () => null }));
vi.mock("../../settings/OrgConfigSection", () => ({ default: () => null }));
vi.mock("../../settings/FullConfigTab", () => ({ default: () => null }));
vi.mock("../../settings/batch/BatchJobsTab", () => ({ default: () => null }));

const REMEDY =
  "Gateway authentication failed. Ask your administrator to check the gateway credentials.";
// The fixed frontend fallback deliberately reuses the existing backend remedy.
const FALLBACK = REMEDY;
const UNSAFE = "private upstream response: credential=synthetic-do-not-show";
const originalAdapter = client.defaults.adapter;
const transport = vi.fn();
let setSessionSpy;
let clearTokensSpy;
let refreshSpy;
let capturedError;

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  auth.setIsRefreshing(false);
  auth.setSession("fake-valid-fa-access");
  setSessionSpy = vi.spyOn(auth, "setSession");
  clearTokensSpy = vi.spyOn(auth, "clearTokens");
  refreshSpy = vi.spyOn(auth, "refreshTokenRequest");
  transport.mockReset();
  client.defaults.adapter = transport;
});

afterEach(() => {
  cleanup();
  client.defaults.adapter = originalAdapter;
  vi.restoreAllMocks();
  auth.setSession(null);
  auth.clearTokens();
  auth.setIsRefreshing(false);
});

function rejectMutation(status, code, message) {
  transport.mockImplementation(async (config) => {
    if (config.method === "get") {
      return {
        status: 200,
        statusText: "OK",
        config,
        headers: {},
        data: { result: [] },
      };
    }
    // Generic-401 controls inspect caller copy after an already-attempted
    // refresh. Gateway cases do NOT set _retry: the real exemption must work.
    if (code !== "gateway_request_rejected") config._retry = true;
    capturedError = new AxiosError(
      `Request failed with status code ${status}`,
      AxiosError.ERR_BAD_REQUEST,
      config,
      undefined,
      {
        status,
        config,
        headers: {},
        data: {
          status: false,
          type: "authentication_error",
          code,
          message,
          detail: message,
          error: message,
          result: message,
        },
      },
    );
    throw capturedError;
  });
}

function expectSessionPreserved() {
  expect(setSessionSpy).not.toHaveBeenCalled();
  expect(clearTokensSpy).not.toHaveBeenCalled();
  expect(refreshSpy).not.toHaveBeenCalled();
  expect(resetUser).not.toHaveBeenCalled();
  expect(client.defaults.headers.common.Authorization).toBe(
    "Bearer fake-valid-fa-access",
  );
  expect(capturedError.config._retry).toBeUndefined();
}

async function submit(surface) {
  const user = userEvent.setup();
  const onClose = vi.fn();
  const component =
    surface === "create" ? (
      <CreateKeyDialog
        open
        onClose={onClose}
        onCreated={vi.fn()}
        gatewayId="fixture-gateway"
      />
    ) : surface === "edit" ? (
      <EditKeyDialog
        open
        onClose={onClose}
        keyData={{ id: "key-1", name: "Test key" }}
        gatewayId="fixture-gateway"
      />
    ) : (
      <KeyManagementSection />
    );
  renderPage(<SnackbarProvider maxSnack={3}>{component}</SnackbarProvider>, {
    route: "/dashboard/gateway/keys",
  });
  if (surface === "create") {
    await user.type(screen.getByRole("textbox", { name: /Name/ }), "Test key");
    await user.click(screen.getByRole("button", { name: "Create" }));
  } else {
    await user.click(
      screen.getByRole("button", {
        name: surface === "edit" ? "Save Changes" : "Sync from gateway",
      }),
    );
  }
  await waitFor(() =>
    expect(
      transport.mock.calls.some(([config]) => config.method !== "get"),
    ).toBe(true),
  );
  return onClose;
}

async function expectVisibleMessage(surface, message) {
  if (surface === "sync") {
    expect(await screen.findByText(message)).toBeVisible();
  } else {
    const dialog = screen.getByRole("dialog");
    expect(await within(dialog).findByText(message)).toBeVisible();
    if (surface === "edit") {
      // Edit has both inline feedback and its existing error snackbar.
      await waitFor(() => expect(screen.getAllByText(message)).toHaveLength(2));
    }
  }
}

describe.each(["create", "edit", "sync"])(
  "%s key gateway authentication feedback",
  (surface) => {
    it("renders the allowlisted administrator remedy without ending the FA session", async () => {
      rejectMutation(401, "gateway_request_rejected", REMEDY);
      const onClose = await submit(surface);
      await expectVisibleMessage(surface, REMEDY);
      expectSessionPreserved();
      expect(onClose).not.toHaveBeenCalled();
    });

    it("renders the fixed fallback, never arbitrary server text, and preserves the session", async () => {
      rejectMutation(401, "gateway_request_rejected", UNSAFE);
      await submit(surface);
      await expectVisibleMessage(surface, FALLBACK);
      expect(screen.queryByText(UNSAFE)).not.toBeInTheDocument();
      expectSessionPreserved();
    });

    it.each([401, 500])(
      "keeps today's exact copy for an ordinary HTTP %s",
      async (status) => {
        const message = "Legacy request failed";
        rejectMutation(
          status,
          status === 401 ? "authentication_failed" : "server_error",
          message,
        );
        await submit(surface);
        if (surface === "sync") {
          expect(
            await screen.findByText("Failed to sync keys from the gateway"),
          ).toBeVisible();
        } else {
          expect(
            await within(screen.getByRole("dialog")).findByText(message),
          ).toBeVisible();
          if (surface === "edit") {
            expect(
              await screen.findByText("Failed to update API key"),
            ).toBeVisible();
          }
        }
        expect(screen.queryByText(REMEDY)).not.toBeInTheDocument();
      },
    );
  },
);

describe.each([
  ["Health Check", "Health check failed", "Health check not authorized"],
  ["Reload Config", "Failed to reload config", "Config reload not authorized"],
])(
  "settings %s gateway authentication feedback",
  (button, legacyCopy, unauthorizedCopy) => {
    async function runAction() {
      renderPage(
        <SnackbarProvider>
          <GatewaySettingsSection />
        </SnackbarProvider>,
        {
          route: "/dashboard/gateway/settings",
        },
      );
      await userEvent
        .setup()
        .click(screen.getByRole("button", { name: button }));
    }

    it.each([REMEDY, UNSAFE])(
      "renders safe remedy for classified response %s without logout",
      async (message) => {
        rejectMutation(401, "gateway_request_rejected", message);
        await runAction();
        expect(await screen.findByText(REMEDY)).toBeVisible();
        expect(screen.queryByText(UNSAFE)).not.toBeInTheDocument();
        expectSessionPreserved();
      },
    );

    it.each([401, 500])(
      "retains today's action copy for HTTP %s",
      async (status) => {
        rejectMutation(status, "ordinary_error", "Legacy request failed");
        await runAction();
        expect(
          await screen.findByText(
            status === 401 ? unauthorizedCopy : legacyCopy,
          ),
        ).toBeVisible();
        expect(screen.queryByText(REMEDY)).not.toBeInTheDocument();
      },
    );
  },
);
