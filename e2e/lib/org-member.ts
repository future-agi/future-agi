// A member of a flow's own organization, invited by its owner through the public
// invite API and signed in with the invitation's own link, as H1's ScopeActors
// invites its Member and Viewer (lib/scope-actors.ts): no SQL, no mail shortcut.
// For flows on the worker's actor (lib/fixtures.ts) that need a second role.
import { expect, request as pwRequest, type Browser, type BrowserContext } from '@playwright/test';
import { ApiClient, type Tokens } from './api-client';
import { authInitScript } from './auth';
import { E2E } from './env';
import type { TestActor } from './provisioning';

/** Organization levels (futureagi/tfc/constants/levels.py): Viewer 1, Member 3. */
export type MemberLevel = 1 | 3;

export interface OrgMember {
  email: string; tokens: Tokens; organizationId: string; workspaceId: string; api: ApiClient;
  /** A browser context signed in as the member, in the owner's workspace. */
  browser(browser: Browser): Promise<BrowserContext>;
  dispose(): Promise<void>;
}

interface Invited { result: { invited: string[]; invites?: { email: string; invite_link: string }[] } }

/** Invites `label` into `owner`'s organization and workspace at `level`, and accepts. */
export async function inviteOrgMember(owner: TestActor, label: string, level: MemberLevel): Promise<OrgMember> {
  // accounts/serializers/rbac.py InviteCreateSerializer; accounts/utils.py builds the link.
  const email = `${label}@futureagi.com`;
  const invited = await owner.api.post<Invited>('/accounts/organization/invite/', {
    emails: [email], org_level: level,
    workspace_access: [{ workspace_id: owner.workspaceId, level }],
  });
  expect(invited.result.invited).toEqual([email]);
  const link = invited.result.invites?.find((i) => i.email === email)?.invite_link;
  if (!link) throw new Error('the invite answered no invitation link');
  // The stack's APP_URL is unset, so the link reads "None/auth/..."; only its
  // route parameters are used, against E2E.apiUrl.
  const match = new URL(link.startsWith('None/auth/') ? link.slice(4) : link, E2E.appUrl).pathname
    .match(/^\/auth\/jwt\/invitation\/accept\/([^/]+)\/([^/]+)\/?$/);
  if (!match) throw new Error('unexpected invitation link shape');
  const req = await pwRequest.newContext({ baseURL: E2E.apiUrl });
  const path = `${E2E.apiUrl}/accounts/accept-invitation/${match[1]}/${match[2]}/`;
  expect((await req.get(path)).status()).toBe(200);
  const password = `E2E-${label}-pw!`;
  const accepted = await req.post(path, { data: { new_password: password, repeat_password: password } });
  expect(accepted.status(), await accepted.text()).toBe(200);
  const tokens = (await accepted.json()) as Tokens;
  const contexts: BrowserContext[] = [];
  return {
    email, tokens, organizationId: owner.organizationId, workspaceId: owner.workspaceId,
    api: new ApiClient(req, E2E.apiUrl).withAuth(tokens, owner.organizationId, owner.workspaceId),
    async browser(browser: Browser) {
      const context = await browser.newContext({ baseURL: E2E.appUrl });
      contexts.push(context);
      await context.addInitScript(authInitScript, { access: tokens.access, refresh: tokens.refresh,
        organizationId: owner.organizationId, workspaceId: owner.workspaceId });
      // WorkspaceContext.jsx rejects stored workspace IDs without the org marker.
      await context.addInitScript((orgId) => sessionStorage.setItem('workspaceOrgId', orgId), owner.organizationId);
      return context;
    },
    async dispose() {
      for (const context of contexts) await context.close();
      await req.dispose();
    },
  };
}
