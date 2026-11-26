import type { APIRequestContext } from '@playwright/test';
import { ApiClient, Tokens, annotateRetry, isResponseLessReset } from './api-client';
import { E2E } from './env';

export interface TestActor {
  email: string; password: string;
  tokens: Tokens;
  organizationId: string; workspaceId: string;
  apiKey: string; secretKey: string;
  api: ApiClient;
}

export interface UserInfo {
  organization: { id: string };
  default_workspace_id: string | null;
  restrict_simulate_to_environments?: boolean;
}
interface KeysEnvelope { status: string; data: { api_key: string; secret_key: string } }

const SIGNUP_PATH = '/accounts/e2e/clean-signup/';

export async function provisionActor(req: APIRequestContext, label: string,
  options: { keyAdministrator?: boolean;
    createAccount?: (account: { email: string; fullName: string; password: string }) => Promise<void> } = {}): Promise<TestActor> {
  const anon = new ApiClient(req, E2E.apiUrl);
  const signUp = async () => {
    // The domain gives the auto-created organization a stable name.
    // Freshness must not depend on the caller's label: parallel workers can
    // provision in the same millisecond.
    const runId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    const identity = { email: `e2e-${label}-${runId}@futureagi.com`, password: `E2e-${label}-${runId}` };
    if (options.createAccount) {
      await options.createAccount({ ...identity, fullName: `E2E ${label}` });
      return identity;
    }
    // Only the test image exposes this route. Signup flows use the public route.
    await anon.post(SIGNUP_PATH, { ...identity, full_name: `E2E ${label}`,
      ...(options.keyAdministrator ? { key_administrator: true } : {}) });
    return identity;
  };
  // The signup is a POST, so ApiClient never replays it. When the connection
  // is reset before any response, it is unknown whether the account exists,
  // so the one retry signs up a NEW identity instead of repeating the first.
  // Only that reset is retried: an HTTP response, a timeout or any other
  // error fails the actor, and so does a second reset.
  let identity;
  try {
    identity = await signUp();
  } catch (error) {
    if (!isResponseLessReset(error)) throw error;
    annotateRetry('post', SIGNUP_PATH, error);
    identity = await signUp();
  }
  const { email, password } = identity;
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
