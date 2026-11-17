import React from "react";
import PropTypes from "prop-types";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import axiosPackage from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "src/auth/context/jwt/auth-provider";
import { useAuthContext } from "src/auth/hooks/use-auth-context";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";
import axios, { endpoints } from "src/utils/axios";
import { SS_KEY_ORG_ID, SS_KEY_WORKSPACE_ID } from "src/utils/sessionKeys";

import {
  fakeApi as fakeDeployments,
  renderDeployments,
} from "../../deployments/__tests__/helpers";
import { session, workspace } from "../../deployments/__tests__/session-mock";
import { forgetKey, heldKey } from "../keyStore";

// P11 fix round 2 (verifier verdict on b58be510a, the blocking finding): the
// panel's personal key was bound to the organization only, so it survived
// sign-out in the tab's memory and the next person to sign in there, in the
// same organization, was shown it and could reveal it. These run the real
// AuthProvider (its login and logout), the real Deployments list page and the
// real Code drawer; only the network and the analytics calls are stood in for.

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspace,
}));
vi.mock("src/sections/omega/shared", async (importOriginal) => ({
  ...(await importOriginal()),
  useOmegaSession: () => session,
}));
vi.mock("src/utils/Mixpanel", async (importOriginal) => ({
  ...(await importOriginal()),
  identifyUser: vi.fn(),
  resetUser: vi.fn(),
}));
vi.mock("src/utils/PostHog", async (importOriginal) => ({
  ...(await importOriginal()),
  identifyPostHogUser: vi.fn(),
  resetPostHogUser: vi.fn(),
}));

const { default: DeploymentsListPage } = await import(
  "../../deployments/DeploymentsListPage"
);

const ORG = "22222222-2222-4222-8222-222222222222";
const WS = "33333333-3333-4333-8333-333333333333";
const OWNER_A = {
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  email: "owner-a@example.com",
  organization: { id: ORG, name: "Org X" },
  organization_role: "Owner",
};
const MEMBER_B = {
  id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  email: "member-b@example.com",
  organization: { id: ORG, name: "Org X" },
  organization_role: "Member",
};
const TOKENS = { "token-a": OWNER_A, "token-b": MEMBER_B };
const KEY = {
  key_id: "66666666-6666-4666-8666-666666666666",
  key_name: "Code snippets 2026-10-22 14:05:09",
  api_key: "e2e2e2e2e2e2e2e2e2e2e2e2e2e2ghij",
  secret_key: "f4f4f4f4f4f4f4f4f4f4f4f4f4f4opqr",
};

let posted;
let ctx;
let spies = [];

/** The app's shell: the page only while someone is signed in, and a way to leave it. */
function Shell({ page }) {
  ctx = useAuthContext();
  if (!ctx.authenticated) return <div data-testid="signed-out" />;
  return page;
}
Shell.propTypes = { page: PropTypes.node };

function renderApp() {
  return renderDeployments(
    <AuthProvider>
      <Shell page={<DeploymentsListPage />} />
    </AuthProvider>,
    { api: fakeDeployments() },
  );
}

/** Password sign-in as the login view does it: login(response), no reload. */
async function signIn(token) {
  await act(async () => {
    await ctx.login({
      status: 200,
      data: { access: token, refresh: `${token}-refresh` },
    });
  });
}

async function signOut() {
  await act(async () => {
    await ctx.logout().catch(() => {});
  });
}

async function openCode() {
  fireEvent.click(await screen.findByTestId("code-open"));
  return screen.findByTestId("code-drawer");
}

async function closeCode() {
  fireEvent.click(screen.getByTestId("code-close"));
  await waitFor(() => expect(screen.queryByTestId("code-drawer")).toBeNull());
}

/** As the person who made it: a key on the panel, shown masked. */
async function ownerMakesAKey() {
  await openCode();
  fireEvent.click(screen.getByTestId("code-create-key"));
  await screen.findByTestId("code-reveal-key");
  expect(screen.getByTestId("code-key-strip").dataset.keyState).toBe("masked");
  expect(heldKey()).not.toBeNull();
}

/** What the next person must not get: no held key, no trace of it, the shell form. */
function expectNoKeyOf(person) {
  expect(screen.getByTestId("code-key-strip").dataset.keyState).toBe("none");
  expect(screen.queryByTestId("code-reveal-key")).toBeNull();
  expect(screen.queryByTestId("code-forget-key")).toBeNull();
  expect(screen.queryByTestId("code-key-name")).toBeNull();
  const page = document.body.textContent;
  expect(page).not.toContain(KEY.key_name);
  expect(page).not.toContain(KEY.api_key.slice(-4));
  expect(page).not.toContain(KEY.secret_key.slice(-4));
  expect(document.body.innerHTML).not.toContain(KEY.api_key);
  const snippet = screen.getByTestId("code-snippet").textContent;
  expect(snippet).toContain('"X-Api-Key: $FI_API_KEY"');
  expect(snippet).toContain('"X-Secret-Key: $FI_SECRET_KEY"');
  expect(heldKey(), person).toBeNull();
}

beforeEach(() => {
  vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
  Object.assign(session, { state: "ready", code: null, message: null });
  localStorage.clear();
  sessionStorage.clear();
  sessionStorage.setItem(SS_KEY_ORG_ID, ORG);
  sessionStorage.setItem(SS_KEY_WORKSPACE_ID, WS);
  forgetKey();
  posted = [];
  const userInfo = vi
    .spyOn(axiosPackage, "get")
    .mockImplementation(async (url, config) => {
      const token = config?.headers?.Authorization?.replace("Bearer ", "");
      if (!url.endsWith(endpoints.auth.me) || !TOKENS[token])
        throw new Error(`unexpected GET ${url}`);
      return { data: { ...TOKENS[token] } };
    });
  const reads = vi.spyOn(axios, "get").mockImplementation(async (url) => {
    if (url.includes("/api/capabilities/"))
      return { data: capabilitiesAnswer({ parts: [] }) };
    throw new Error(`unexpected GET ${url}`);
  });
  const writes = vi
    .spyOn(axios, "post")
    .mockImplementation(async (url, body) => {
      posted.push(url);
      if (url === endpoints.keys.generateSecretKey)
        return { data: { status: true, result: { ...KEY } } };
      if (url === endpoints.auth.logout) return { data: {} };
      throw new Error(`unexpected POST ${url} ${JSON.stringify(body)}`);
    });
  spies = [userInfo, reads, writes];
});
afterEach(() => {
  // Only these spies: the test setup's own stand-ins (matchMedia) stay.
  for (const spy of spies) spy.mockRestore();
  vi.unstubAllEnvs();
  localStorage.clear();
  sessionStorage.clear();
  forgetKey();
});

describe("a code panel's key, when someone else signs in to the tab", () => {
  it("is gone after the Owner signs out, and the Member who signs in next in the same organization is never shown it", async () => {
    renderApp();
    await screen.findByTestId("signed-out");
    await signIn("token-a");
    await ownerMakesAKey();
    expect(posted).toContain(endpoints.keys.generateSecretKey);

    // The Owner leaves the page, then signs out as the profile menu does
    // (logout, then an in-app navigation; no reload).
    await closeCode();
    await signOut();
    await screen.findByTestId("signed-out");
    expect(posted).toContain(endpoints.auth.logout);
    // Signing out alone drops it, with no panel open to notice.
    expect(heldKey()).toBeNull();

    // A Member of the same organization signs in on the same tab.
    await signIn("token-b");
    expect(sessionStorage.getItem(SS_KEY_ORG_ID)).toBe(ORG);
    await openCode();
    expectNoKeyOf("member");
  });

  it("is dropped at sign-out even when the sign-out request fails", async () => {
    renderApp();
    await screen.findByTestId("signed-out");
    await signIn("token-a");
    await ownerMakesAKey();
    await closeCode();
    axios.post.mockImplementation(async (url) => {
      if (url === endpoints.auth.logout) throw new Error("network down");
      throw new Error(`unexpected POST ${url}`);
    });
    await signOut();
    expect(heldKey()).toBeNull();
  });

  it("is not shown to the next person even without a sign-out in between (bound to its maker, not to the tab)", async () => {
    renderApp();
    await screen.findByTestId("signed-out");
    await signIn("token-a");
    await ownerMakesAKey();
    // Another person signs in over the Owner's session in this tab, with the
    // Owner's drawer still open: logout never ran, so only the key's owner
    // check stands between them and the key.
    await signIn("token-b");
    await waitFor(() =>
      expect(screen.getByTestId("code-key-strip").dataset.keyState).toBe(
        "none",
      ),
    );
    expectNoKeyOf("member, drawer left open");
    await closeCode();
    await openCode();
    expectNoKeyOf("member, drawer opened again");
    expect(posted.filter((url) => url === endpoints.auth.logout)).toEqual([]);
  });

  it("is still the Owner's own on their next page, and makes no key while the organization is unknown", async () => {
    renderApp();
    await screen.findByTestId("signed-out");
    await signIn("token-a");
    await ownerMakesAKey();
    expect(heldKey()).toMatchObject({
      userId: OWNER_A.id,
      organizationId: ORG,
    });
    await closeCode();
    await openCode();
    expect(screen.getByTestId("code-key-strip").dataset.keyState).toBe(
      "masked",
    );
    fireEvent.click(screen.getByTestId("code-forget-key"));
    expect(heldKey()).toBeNull();
    await closeCode();

    // No organization stored: no key is made (it would belong to no
    // organization the panels could check), and nothing is sent.
    sessionStorage.removeItem(SS_KEY_ORG_ID);
    posted = [];
    await openCode();
    fireEvent.click(screen.getByTestId("code-create-key"));
    expect(await screen.findByTestId("code-create-key-error")).toBeTruthy();
    expect(posted).toEqual([]);
    expect(heldKey()).toBeNull();
  });
});
