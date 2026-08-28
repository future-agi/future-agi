import type { APIRequestContext } from '@playwright/test';
import { ApiClient, Tokens } from './api-client';
import { E2E } from './env';
import { setKnownPassword } from './ee-password';

export interface TestActor {
  email: string; password: string;
  tokens: Tokens;
  organizationId: string; workspaceId: string;
  apiKey: string; secretKey: string;
  api: ApiClient;
}

interface UserInfo { organization: { id: string }; default_workspace_id: string | null }
interface KeysEnvelope { status: string; data: { api_key: string; secret_key: string } }

export async function provisionActor(req: APIRequestContext, label: string): Promise<TestActor> {
  // futureagi.com domain: belt (special-email recaptcha bypass) to the
  // localhost-Host suspenders; also gives the auto-created org a stable name.
  // Freshness must not depend on the caller's label: parallel workers can
  // provision in the same millisecond.
  const runId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  const email = `e2e-${label}-${runId}@futureagi.com`;
  const password = `E2e-${label}-${runId}`;
  const anon = new ApiClient(req, E2E.apiUrl);

  // OSS signup accepts `password` and returns the login payload straight away.
  // EE/cloud drops `password` from its field allowlist and returns only a
  // "check your email" message, leaving the (already active) account with a
  // server-generated password — so there is nothing to log in with until we
  // set one. Detecting it from the response keeps this a real toggle: the same
  // harness provisions against either mode with no env flag to keep in sync.
  // NB: signup wraps its payload in `result` (unlike /accounts/token/, which
  // returns the tokens flat), so the OSS auto-login shows up as result.access.
  const signup = await anon.post<{ result?: Partial<Tokens> }>(
    '/accounts/signup/', { email, full_name: `E2E ${label}`, password },
  );
  if (!signup?.result?.access) await setKnownPassword(email, password);

  const tokens = await anon.post<Tokens>('/accounts/token/', { email, password, remember_me: true });

  let api = anon.withAuth(tokens);
  let info = await api.get<UserInfo>('/accounts/user-info/');
  if (!info.default_workspace_id) info = await api.get<UserInfo>('/accounts/user-info/');
  if (!info.default_workspace_id) throw new Error(`no default workspace for ${email} — see Task 3 Step 3`);

  const organizationId = info.organization.id;
  const workspaceId = info.default_workspace_id;
  api = anon.withAuth(tokens, organizationId, workspaceId);
  const keys = await api.get<KeysEnvelope>('/accounts/keys/');

  return { email, password, tokens, organizationId, workspaceId,
           apiKey: keys.data.api_key, secretKey: keys.data.secret_key, api };
}
